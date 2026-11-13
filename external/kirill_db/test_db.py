import uuid
from datetime import datetime, timezone, timedelta

import pytest
from sqlalchemy import select, create_engine, event as sa_event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from db import (
    Base,
    utcnow,
    User,
    SimulationRun,
    GlucoseReading,
    GlucoseEvent,
    Notification,
    AnalysisState,
    save_tick,
    StaleStateError,
)


@pytest.fixture(scope="function")
def engine():
    eng = create_engine("sqlite:///:memory:", echo=False)

    @sa_event.listens_for(eng, "connect")
    def _fk(conn, _rec):
        cur = conn.cursor()
        cur.execute("PRAGMA foreign_keys=ON")
        cur.close()

    Base.metadata.create_all(eng)
    yield eng
    Base.metadata.drop_all(eng)
    eng.dispose()


@pytest.fixture(scope="function")
def session(engine):
    Session = sessionmaker(bind=engine)
    s = Session()
    yield s
    s.close()


@pytest.fixture
def user(session):
    u = User(display_name="Test User")
    session.add(u)
    session.commit()
    return u


@pytest.fixture
def run(session, user):
    r = SimulationRun(user_id=user.id, dataset_name="test_ds", status="running")
    session.add(r)
    session.commit()
    return r


def _now():
    return datetime.now(timezone.utc)


def _reading(run_id, source_index, value=5.5, offset_minutes=0):
    return GlucoseReading(
        run_id=run_id,
        source_index=source_index,
        measured_at=_now() + timedelta(minutes=offset_minutes),
        value_mmol_l=value,
    )


def _event(run_id, event_uid=None, phase="started", severity="low", status="active"):
    return GlucoseEvent(
        run_id=run_id,
        event_type="hypo",
        event_uid=event_uid or str(uuid.uuid4()),
        phase=phase,
        severity=severity,
        status=status,
        started_at=_now(),
    )


def _state(run_id, last_index=0, rise=0, fall=0, rev=0):
    return AnalysisState(
        run_id=run_id,
        last_source_index=last_index,
        rise_count=rise,
        fall_count=fall,
        revision=rev,
        updated_at=_now(),
    )



class TestSaveAndFetch:

    def test_save_reading(self, session, run):
        r = _reading(run.id, source_index=0)
        session.add(r)
        session.commit()

        fetched = session.query(GlucoseReading).filter_by(run_id=run.id).one()
        assert fetched.source_index == 0
        assert fetched.value_mmol_l == pytest.approx(5.5)

    def test_save_event(self, session, run):
        ev = _event(run.id, phase="started")
        session.add(ev)
        session.commit()

        fetched = session.query(GlucoseEvent).filter_by(run_id=run.id).one()
        assert fetched.phase == "started"
        assert fetched.event_uid is not None

    def test_save_notification(self, session, run):
        ev = _event(run.id)
        session.add(ev)
        session.flush()

        n = Notification(
            run_id=run.id,
            event_id=ev.id,
            title="High glucose",
            body="Your glucose is high",
        )
        session.add(n)
        session.commit()

        fetched = session.query(Notification).filter_by(run_id=run.id).one()
        assert fetched.title == "High glucose"
        assert fetched.push_status == "pending"
        assert fetched.acknowledged_at is None

    def test_save_analysis_state(self, session, run):
        st = _state(run.id, last_index=5, rise=2, fall=1, rev=1)
        session.add(st)
        session.commit()

        fetched = session.get(AnalysisState, run.id)
        assert fetched.last_source_index == 5
        assert fetched.rise_count == 2
        assert fetched.fall_count == 1
        assert fetched.revision == 1


class TestNoDuplicates:

    def test_duplicate_reading_same_run_source_raises(self, session, run):
        r1 = _reading(run.id, source_index=1)
        r2 = _reading(run.id, source_index=1, value=6.0) 
        session.add(r1)
        session.commit()

        session.add(r2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_same_source_index_different_runs_is_ok(self, session, user):
        run1 = SimulationRun(user_id=user.id, dataset_name="ds", status="running")
        run2 = SimulationRun(user_id=user.id, dataset_name="ds", status="running")
        session.add_all([run1, run2])
        session.commit()

        r1 = _reading(run1.id, source_index=0)
        r2 = _reading(run2.id, source_index=0)
        session.add_all([r1, r2])
        session.commit()

        assert session.query(GlucoseReading).count() == 2

    def test_duplicate_event_phase_raises(self, session, run):
        uid = str(uuid.uuid4())
        ev1 = _event(run.id, event_uid=uid, phase="started")
        ev2 = _event(run.id, event_uid=uid, phase="started")  
        session.add(ev1)
        session.commit()

        session.add(ev2)
        with pytest.raises(IntegrityError):
            session.commit()

    def test_different_phases_same_event_uid_allowed(self, session, run):
        uid = str(uuid.uuid4())
        for phase in ("started", "ended", "interrupted", "observed"):
            session.add(_event(run.id, event_uid=uid, phase=phase))
        session.commit()

        count = session.query(GlucoseEvent).filter_by(event_uid=uid).count()
        assert count == 4

    def test_save_tick_duplicate_reading_returns_false(self, session, run):
        r1 = _reading(run.id, source_index=10)
        r2 = _reading(run.id, source_index=10, value=9.0)

        result1 = save_tick(session, run_id=run.id, reading=r1)
        assert result1 is True

        result2 = save_tick(session, run_id=run.id, reading=r2)
        assert result2 is False

        rows = session.query(GlucoseReading).filter_by(run_id=run.id, source_index=10).all()
        assert len(rows) == 1
        assert rows[0].value_mmol_l == pytest.approx(5.5)

class TestAtomicTransaction:

    def test_failed_tick_leaves_no_partial_data(self, session, run):
        r_ok = _reading(run.id, source_index=20)
        save_tick(session, run_id=run.id, reading=r_ok)

        r_dup = _reading(run.id, source_index=20, value=7.0)
        ev = _event(run.id)
        st = _state(run.id, last_index=20, rev=1)

        result = save_tick(session, run_id=run.id, reading=r_dup, events=(ev,), new_state=st)
        assert result is False
        assert session.query(GlucoseEvent).count() == 0
        assert session.get(AnalysisState, run.id) is None

class TestRestartRecovery:

    def test_state_survives_session_recreation(self, engine, user):
        Session = sessionmaker(bind=engine)

        s1 = Session()
        run = SimulationRun(user_id=user.id, dataset_name="recovery_ds", status="running")
        s1.add(run)
        s1.flush()
        run_id = run.id

        st = _state(run_id, last_index=42, rise=3, fall=1, rev=7)
        s1.add(st)
        s1.commit()
        s1.close()

        s2 = Session()
        recovered = s2.get(AnalysisState, run_id)
        assert recovered is not None
        assert recovered.last_source_index == 42
        assert recovered.rise_count == 3
        assert recovered.fall_count == 1
        assert recovered.revision == 7
        s2.close()

    def test_state_updates_without_duplicate(self, session, run):
        r1 = _reading(run.id, source_index=0)
        st1 = _state(run.id, last_index=0, rev=0)
        save_tick(session, run_id=run.id, reading=r1, new_state=st1,
                  expected_revision=0)

        r2 = _reading(run.id, source_index=1, offset_minutes=5)
        st2 = _state(run.id, last_index=1, rise=1, rev=1)
        save_tick(session, run_id=run.id, reading=r2, new_state=st2,
                  expected_revision=1)

        rows = session.query(AnalysisState).filter_by(run_id=run.id).all()
        assert len(rows) == 1
        assert rows[0].last_source_index == 1
        assert rows[0].revision == 2

    def test_multiple_runs_have_independent_states(self, session, user):
        run_a = SimulationRun(user_id=user.id, dataset_name="ds_a", status="running")
        run_b = SimulationRun(user_id=user.id, dataset_name="ds_b", status="running")
        session.add_all([run_a, run_b])
        session.commit()

        st_a = _state(run_a.id, last_index=10, rise=2, rev=5)
        st_b = _state(run_b.id, last_index=99, fall=4, rev=12)
        session.add_all([st_a, st_b])
        session.commit()

        a = session.get(AnalysisState, run_a.id)
        b = session.get(AnalysisState, run_b.id)

        assert a.last_source_index == 10
        assert b.last_source_index == 99
        assert a.revision != b.revision

    def test_readings_isolated_per_run(self, session, user):
        run_a = SimulationRun(user_id=user.id, dataset_name="ds", status="running")
        run_b = SimulationRun(user_id=user.id, dataset_name="ds", status="running")
        session.add_all([run_a, run_b])
        session.commit()

        for i in range(3):
            session.add(_reading(run_a.id, source_index=i, offset_minutes=i))
        session.add(_reading(run_b.id, source_index=0))
        session.commit()

        a_readings = session.query(GlucoseReading).filter_by(run_id=run_a.id).all()
        b_readings = session.query(GlucoseReading).filter_by(run_id=run_b.id).all()
        assert len(a_readings) == 3
        assert len(b_readings) == 1

class TestNotifications:

    def test_notification_read_status_defaults_pending(self, session, run):
        ev = _event(run.id)
        session.add(ev)
        session.flush()

        n = Notification(run_id=run.id, event_id=ev.id, title="T", body="B")
        session.add(n)
        session.commit()

        fetched = session.get(Notification, n.id)
        assert fetched.push_status == "pending"
        assert fetched.acknowledged_at is None

    def test_notification_acknowledge(self, session, run):
        ev = _event(run.id)
        session.add(ev)
        session.flush()

        n = Notification(run_id=run.id, event_id=ev.id, title="T", body="B")
        session.add(n)
        session.commit()

        n.acknowledged_at = _now()
        n.push_status = "acknowledged"
        session.commit()

        fetched = session.get(Notification, n.id)
        assert fetched.push_status == "acknowledged"
        assert fetched.acknowledged_at is not None

    def test_multiple_notifications_per_event(self, session, run):
        ev = _event(run.id)
        session.add(ev)
        session.flush()

        for i in range(3):
            session.add(Notification(run_id=run.id, event_id=ev.id,
                                     title=f"T{i}", body=f"B{i}"))
        session.commit()

        count = session.query(Notification).filter_by(event_id=ev.id).count()
        assert count == 3

    def test_cascade_delete_event_removes_notifications(self, session, run):
        ev = _event(run.id)
        session.add(ev)
        session.flush()
        ev_id = ev.id

        session.add(Notification(run_id=run.id, event_id=ev_id, title="T", body="B"))
        session.commit()

        session.delete(ev)
        session.commit()

        assert session.query(Notification).filter_by(event_id=ev_id).count() == 0

class TestSaveTickPipeline:

    def test_full_tick_persists_everything(self, session, run):
        reading = _reading(run.id, source_index=3, value=11.2)
        ev = _event(run.id, phase="started", severity="high")
        ev._pending_notifications = [
            {"title": "Гипергликемия", "body": "11.2 ммоль/л", "push_status": "pending"}
        ]
        st = _state(run.id, last_index=3, rise=1, rev=1)

        assert save_tick(session, run_id=run.id, reading=reading,
                         events=(ev,), new_state=st) is True

        assert session.query(GlucoseReading).filter_by(run_id=run.id).count() == 1
        assert session.query(GlucoseEvent).filter_by(run_id=run.id).count() == 1

        notif = session.query(Notification).filter_by(run_id=run.id).one()
        assert notif.title == "Гипергликемия"
        assert notif.event_id == ev.id        
        assert notif.acknowledged_at is None

        saved_state = session.get(AnalysisState, run.id)
        assert saved_state.last_source_index == 3
        assert saved_state.rise_count == 1

    def test_tick_increments_run_source_position(self, session, run):
        save_tick(session, run_id=run.id, reading=_reading(run.id, 0), run=run)
        save_tick(session, run_id=run.id, reading=_reading(run.id, 1, offset_minutes=1), run=run)

        session.refresh(run)
        assert run.source_position == 2

    def test_duplicate_tick_does_not_advance_source_position(self, session, run):
        save_tick(session, run_id=run.id, reading=_reading(run.id, 5),
                  run=run, new_state=_state(run.id, last_index=5),
                  expected_revision=0)
        session.refresh(run)
        before = run.source_position

        ok = save_tick(session, run_id=run.id, reading=_reading(run.id, 5, value=8.0),
                       run=run, new_state=_state(run.id, last_index=5, rise=1),
                       expected_revision=1)
        assert ok is False

        session.expire_all()
        assert session.get(SimulationRun, run.id).source_position == before
        assert session.get(AnalysisState, run.id).revision == 1 
    def test_non_duplicate_integrity_error_is_reraised(self, session, run):
        bad = GlucoseReading(run_id=run.id, source_index=1,
                             measured_at=_now(), value_mmol_l=None) 
        with pytest.raises(IntegrityError):
            save_tick(session, run_id=run.id, reading=bad)

    def test_failed_event_tick_leaves_no_partial_data(self, session, run):
        ev1 = _event(run.id, event_uid="E1", phase="started")
        ok1 = save_tick(session, run_id=run.id, reading=_reading(run.id, 0), events=(ev1,))
        assert ok1 is True

        ev2 = _event(run.id, event_uid="E1", phase="started")
        with pytest.raises(IntegrityError):
            save_tick(session, run_id=run.id, reading=_reading(run.id, 1), events=(ev2,))

        assert session.query(GlucoseReading).filter_by(run_id=run.id).count() == 1
        assert session.query(GlucoseEvent).filter_by(run_id=run.id).count() == 1
        assert session.query(Notification).filter_by(run_id=run.id).count() == 0

        ok2 = save_tick(session, run_id=run.id, reading=_reading(run.id, 2))
        assert ok2 is True

    def test_notification_dict_with_run_id_is_safely_filtered(self, session, run):
        ev = _event(run.id)
        ev._pending_notifications = [
            {"run_id": run.id, "title": "T", "body": "B"}
        ]
        assert save_tick(session, run_id=run.id, reading=_reading(run.id, 0),
                         events=(ev,)) is True

        n = session.query(Notification).filter_by(run_id=run.id).one()
        assert n.title == "T"
        assert n.run_id == run.id


class TestSaveTickErrorClassification:
    def test_not_null_run_id_raises_not_swallowed(self, session, run):
        bad = GlucoseReading(run_id=None, source_index=0,
                             measured_at=_now(), value_mmol_l=5.0)
        with pytest.raises(IntegrityError, match="NOT NULL"):
            save_tick(session, run_id=run.id, reading=bad)

    def test_foreign_key_violation_is_reraised(self, session, user):
        other = SimulationRun(user_id=user.id, dataset_name="d")
        session.add(other)
        session.commit()

        bad = GlucoseReading(run_id="no-such-run", source_index=0,
                             measured_at=_now(), value_mmol_l=5.0)
        with pytest.raises(IntegrityError, match="FOREIGN KEY"):
            save_tick(session, run_id="no-such-run", reading=bad)

    def test_not_null_value_violation_is_reraised(self, session, run):
        bad = GlucoseReading(run_id=run.id, source_index=5,
                             measured_at=_now(), value_mmol_l=None)
        with pytest.raises(IntegrityError, match="NOT NULL"):
            save_tick(session, run_id=run.id, reading=bad)


class TestStaleState:

    def test_stale_revision_raises_and_rolls_back_tick(self, session, run):
        save_tick(session, run_id=run.id, reading=_reading(run.id, 0),
                  new_state=_state(run.id, last_index=0, rev=0),
                  expected_revision=0)
        assert session.get(AnalysisState, run.id).revision == 1

        with pytest.raises(StaleStateError):
            save_tick(session, run_id=run.id, reading=_reading(run.id, 1),
                      new_state=_state(run.id, last_index=1, rev=0),
                      expected_revision=0)
            
        assert session.query(GlucoseReading).filter_by(run_id=run.id).count() == 1

        save_tick(session, run_id=run.id, reading=_reading(run.id, 1),
                  new_state=_state(run.id, last_index=1, rev=1),
                  expected_revision=1)
        assert session.get(AnalysisState, run.id).revision == 2
        assert session.query(GlucoseReading).filter_by(run_id=run.id).count() == 2

    def test_state_row_exists_but_no_expected_revision_raises(self, session, run):

        save_tick(session, run_id=run.id, reading=_reading(run.id, 0),
                  new_state=_state(run.id, last_index=0),
                  expected_revision=0)
        with pytest.raises(StaleStateError):
            save_tick(session, run_id=run.id, reading=_reading(run.id, 1),
                      new_state=_state(run.id, last_index=1),
                      expected_revision=None)


class TestOnDiskRestart:

    def test_state_recovered_after_engine_restart(self, tmp_path, run):
        db_file = tmp_path / "restart.db"
        url = f"sqlite:///{db_file}"

        def make_engine():
            eng = create_engine(url, echo=False)

            @sa_event.listens_for(eng, "connect")
            def _fk(conn, _rec):
                cur = conn.cursor()
                cur.execute("PRAGMA foreign_keys=ON")
                cur.close()

            Base.metadata.create_all(eng)
            return eng

        eng1 = make_engine()
        s1 = sessionmaker(bind=eng1)()

        owner = User(display_name="Persisted User")
        s1.add(owner)
        s1.flush()

        sim = SimulationRun(user_id=owner.id, dataset_name="persisted_ds",
                            status="running")
        s1.add(sim)
        s1.flush()
        run_id = sim.id

        save_tick(
            s1, run_id=run_id,
            reading=_reading(run_id, 0, value=6.4),
            events=(_event(run_id, phase="started"),),
            new_state=_state(run_id, last_index=0, rise=1),
            expected_revision=0,
        )
        save_tick(
            s1, run_id=run_id,
            reading=_reading(run_id, 1, value=6.9, offset_minutes=5),
            new_state=_state(run_id, last_index=1, rise=2),
            expected_revision=1,
        )
        s1.commit()
        s1.close()
        eng1.dispose()

        eng2 = make_engine()
        s2 = sessionmaker(bind=eng2)()
        try:
            assert s2.get(SimulationRun, run_id) is not None

            recovered = s2.get(AnalysisState, run_id)
            assert recovered is not None
            assert recovered.last_source_index == 1
            assert recovered.rise_count == 2
            assert recovered.revision == 2

            readings = (s2.query(GlucoseReading)
                          .filter_by(run_id=run_id)
                          .order_by(GlucoseReading.source_index)
                          .all())
            assert [r.source_index for r in readings] == [0, 1]
            assert [round(r.value_mmol_l, 2) for r in readings] == [6.4, 6.9]

            assert s2.query(GlucoseReading).filter_by(run_id=run_id).count() == 2
            assert s2.query(AnalysisState).filter_by(run_id=run_id).count() == 1
            assert s2.query(GlucoseEvent).filter_by(run_id=run_id).count() == 1
        finally:
            s2.close()
            eng2.dispose()

    def test_duplicate_guard_survives_restart(self, tmp_path):
        db_file = tmp_path / "dupe.db"
        url = f"sqlite:///{db_file}"

        eng1 = create_engine(url)
        Base.metadata.create_all(eng1)
        s1 = sessionmaker(bind=eng1)()
        owner = User(display_name="U")
        s1.add(owner)
        s1.flush()
        sim = SimulationRun(user_id=owner.id, dataset_name="d", status="running")
        s1.add(sim)
        s1.flush()
        run_id = sim.id
        save_tick(s1, run_id=run_id, reading=_reading(run_id, 7))
        s1.commit()
        s1.close()
        eng1.dispose()

        eng2 = create_engine(url)
        s2 = sessionmaker(bind=eng2)()
        try:
            again = save_tick(s2, run_id=run_id, reading=_reading(run_id, 7, value=9.9))
            assert again is False
            rows = s2.query(GlucoseReading).filter_by(run_id=run_id).all()
            assert len(rows) == 1
            assert rows[0].value_mmol_l == pytest.approx(5.5)
        finally:
            s2.close()
            eng2.dispose()


class TestTransactionSemantics:

    def test_save_tick_inside_open_transaction_does_not_autocommit(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'tx.db'}"

        eng1 = create_engine(url)

        @sa_event.listens_for(eng1, "connect")
        def _fk(conn, _rec):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        Base.metadata.create_all(eng1)
        s = sessionmaker(bind=eng1)()

        u = User(display_name="Tx User")
        s.add(u)
        s.flush()

        sim = SimulationRun(user_id=u.id, dataset_name="tx_ds", status="running")
        s.add(sim)
        s.flush()
        run_id = sim.id

        assert s.in_transaction() is True
        assert save_tick(s, run_id=run_id, reading=_reading(run_id, 0)) is True
        
        eng2 = create_engine(url)
        s2 = sessionmaker(bind=eng2)()
        try:
            assert s2.query(GlucoseReading).count() == 0
        finally:
            s2.close()
            eng2.dispose()

        assert s.in_transaction() is True

        s.commit()

        eng3 = create_engine(url)
        s3 = sessionmaker(bind=eng3)()
        try:
            assert s3.query(GlucoseReading).count() == 1
        finally:
            s3.close()
            eng3.dispose()
            s.close()
            eng1.dispose()

    def test_db_cascade_on_run_delete(self, tmp_path):

        db_file = tmp_path / "cascade.db"
        url = f"sqlite:///{db_file}"

        eng1 = create_engine(url)

        @sa_event.listens_for(eng1, "connect")
        def _fk(conn, _rec):
            cur = conn.cursor()
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()

        Base.metadata.create_all(eng1)
        s1 = sessionmaker(bind=eng1)()
        u = User(display_name="Cascade User")
        s1.add(u)
        s1.flush()
        sim = SimulationRun(user_id=u.id, dataset_name="casc_ds", status="running")
        s1.add(sim)
        s1.flush()
        run_id = sim.id

        save_tick(s1, run_id=run_id, reading=_reading(run_id, 0))
        s1.commit()
        assert s1.query(GlucoseReading).count() == 1

        from sqlalchemy import text
        s1.execute(text("DELETE FROM simulation_runs WHERE id=:i"), {"i": run_id})
        s1.commit()

        eng2 = create_engine(url)
        s2 = sessionmaker(bind=eng2)()
        try:
            assert s2.query(GlucoseReading).filter_by(run_id=run_id).count() == 0
            assert s2.query(AnalysisState).filter_by(run_id=run_id).count() == 0
        finally:
            s2.close()
            eng2.dispose()


class TestUtcConvention:
    def test_aware_input_stored_as_utc(self, session, run):
        aware = datetime(2026, 11, 13, 12, 0, tzinfo=timezone.utc)
        session.add(GlucoseReading(run_id=run.id, source_index=0,
                                   measured_at=aware, value_mmol_l=5.5))
        session.commit()
        session.expire_all()

        got = session.query(GlucoseReading).filter_by(run_id=run.id).one()
        assert got.measured_at.tzinfo == timezone.utc
        assert got.measured_at == aware

    def test_offset_converted_not_truncated(self, session, run):
        msk = datetime(2026, 11, 13, 12, 0, tzinfo=timezone(timedelta(hours=3)))
        session.add(GlucoseReading(run_id=run.id, source_index=0,
                                   measured_at=msk, value_mmol_l=5.5))
        session.commit()
        session.expire_all()

        got = session.query(GlucoseReading).filter_by(run_id=run.id).one()
        assert got.measured_at == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
        assert got.measured_at.tzinfo == timezone.utc

    def test_defaults_are_aware(self, session, run):
        session.add(GlucoseReading(run_id=run.id, source_index=0,
                                   measured_at=datetime(2026, 11, 13, 9, 0),
                                   value_mmol_l=5.5))
        session.commit()
        session.expire_all()

        got = session.query(GlucoseReading).filter_by(run_id=run.id).one()
        assert got.received_at.tzinfo == timezone.utc
        assert got.measured_at == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)

    def test_stored_value_comparable_with_utcnow(self, session, run):
        session.add(GlucoseReading(run_id=run.id, source_index=0,
                                   measured_at=datetime(2026, 11, 13, 9, 0),
                                   value_mmol_l=5.5))
        session.commit()
        session.expire_all()

        got = session.query(GlucoseReading).filter_by(run_id=run.id).one()
        assert (utcnow() - got.received_at).total_seconds() >= 0

    def test_aware_default_survives_on_disk_restart(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'aware.db'}"
        eng1 = create_engine(url)
        Base.metadata.create_all(eng1)
        s1 = sessionmaker(bind=eng1)()
        u = User(display_name="U")
        s1.add(u); s1.flush()
        sim = SimulationRun(user_id=u.id, dataset_name="d", status="running")
        s1.add(sim); s1.flush()
        save_tick(s1, run_id=sim.id,
                  reading=GlucoseReading(
                      run_id=sim.id, source_index=0,
                      measured_at=datetime(2026, 11, 13, 12, 0,
                                           tzinfo=timezone(timedelta(hours=3))),
                      value_mmol_l=5.5))
        s1.commit(); s1.close(); eng1.dispose()

        eng2 = create_engine(url)
        s2 = sessionmaker(bind=eng2)()
        try:
            got = s2.query(GlucoseReading).one()
            assert got.measured_at.tzinfo == timezone.utc
            assert got.measured_at == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
            assert got.received_at.tzinfo == timezone.utc
            assert isinstance(utcnow() - got.measured_at, timedelta)
        finally:
            s2.close(); eng2.dispose()


class TestEventRecovery:
    def test_full_event_survives_restart(self, tmp_path):
        url = f"sqlite:///{tmp_path / 'events.db'}"
        eng1 = create_engine(url)
        Base.metadata.create_all(eng1)
        s1 = sessionmaker(bind=eng1)()
        owner = User(display_name="U")
        sim = SimulationRun(user=owner, dataset_name="ds", status="running")
        s1.add(sim)
        s1.commit()
        run_id = sim.id
        measured = datetime(2026, 11, 13, 12, 0,
                            tzinfo=timezone(timedelta(hours=3)))
        ev = _event(run_id)
        ev.measured_at = measured
        ev.glucose_mmol_l = 3.2
        ev.details = {"trigger": "low", "samples": [3.4, 3.2]}
        ev.origin = "algorithm"
        ev.priority_hint = 2
        assert save_tick(s1, run_id=run_id,
                         reading=GlucoseReading(run_id=run_id, source_index=0,
                                                measured_at=measured, value_mmol_l=3.2),
                         events=(ev,)) is True
        s1.commit()
        s1.close()
        eng1.dispose()

        eng2 = create_engine(url)
        s2 = sessionmaker(bind=eng2)()
        try:
            got = s2.query(GlucoseEvent).one()
            assert got.measured_at == datetime(2026, 11, 13, 9, 0, tzinfo=timezone.utc)
            assert got.measured_at.tzinfo == timezone.utc
            assert got.glucose_mmol_l == pytest.approx(3.2)
            assert got.details == {"trigger": "low", "samples": [3.4, 3.2]}
            assert got.origin == "algorithm"
            assert got.priority_hint == 2
        finally:
            s2.close()
            eng2.dispose()

    def test_duplicate_event_does_not_hide_error_or_persist_reading(self, session, run):
        ev = _event(run.id, event_uid="same", phase="started")
        assert save_tick(session, run_id=run.id, reading=_reading(run.id, 0),
                         events=(ev,)) is True
        with pytest.raises(IntegrityError):
            save_tick(session, run_id=run.id, reading=_reading(run.id, 1),
                      events=(_event(run.id, event_uid="same", phase="started"),))
        assert session.query(GlucoseReading).filter_by(run_id=run.id).count() == 1
        assert session.query(GlucoseEvent).filter_by(run_id=run.id).count() == 1

def test_utc_datetime_timezone_aware(session):
    now_utc = datetime.now(timezone.utc)
    
    user = User(display_name="Test User", created_at=now_utc)
    session.add(user)
    session.commit()

    session.expire_all()

    loaded_user = session.get(User, user.id)
    assert loaded_user.created_at is not None
    assert loaded_user.created_at.tzinfo is not None
    assert loaded_user.created_at.utcoffset() == timedelta(0)


def test_glucose_event_full_fields(session):
    user = User(display_name="Test User")
    session.add(user)
    session.flush()

    run = SimulationRun(user_id=user.id, dataset_name="ds1")
    session.add(run)
    session.flush()

    event_time = datetime.now(timezone.utc)
    event = GlucoseEvent(
        run_id=run.id,
        event_type="hyper",
        event_uid="test-uid-123",
        phase="started",
        severity="moderate",
        status="active",
        started_at=event_time,
        ended_at=None,
        measured_at=event_time,
        glucose_mmol_l=11.5,
        details={"reason": "high carbs", "confidence": 0.95},
        origin="algorithm",
        priority_hint=10,
    )
    session.add(event)
    session.commit()

    session.expire_all()
    loaded_event = session.execute(
        select(GlucoseEvent).filter_by(event_uid="test-uid-123")
    ).scalar_one()

    assert loaded_event.measured_at is not None
    assert loaded_event.measured_at.tzinfo is not None
    assert loaded_event.measured_at.utcoffset() == timedelta(0)
    assert loaded_event.glucose_mmol_l == 11.5
    assert loaded_event.details == {"reason": "high carbs", "confidence": 0.95}
    assert loaded_event.origin == "algorithm"
    assert loaded_event.priority_hint == 10