"""Public-mode isolation/security and restart tests, using temporary SQLite only."""
import os
import tempfile
import time
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy import select, func

from app.db.database import Database
from app.main import create_app
from app.services.demo_sessions import COOKIE_NAME, MAX_RUNS, SESSION_IDLE_SECONDS
from external.kirill_db.db import User, SimulationRun
from simulator import load_readings
from app.api.schemas.reading import Reading


class PublicDemoTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.path = Path(self.directory.name) / "demo.sqlite3"
        self.environment = patch.dict(os.environ, {"CGM_PUBLIC_DEMO": "1"})
        self.environment.start()
        self.clients = []
        self.open_app()

    def open_app(self, cookies=None):
        self.database = Database(f"sqlite:///{self.path}")
        self.app = create_app(self.database)
        self.client = TestClient(self.app, base_url="https://demo.test")
        if cookies:
            for name, value in cookies.items():
                self.client.cookies.set(name, value, domain="demo.test", path="/")
        self.client.__enter__()
        self.clients.append(self.client)

    def tearDown(self):
        self.client.__exit__(None, None, None)
        for client in self.clients:
            client.close()
        self.database.dispose()
        self.environment.stop()
        self.directory.cleanup()

    def visitor(self):
        client = TestClient(self.app, base_url="https://demo.test", headers={"X-CGM-Demo": "1"})
        self.clients.append(client)
        response = client.get("/api/v1/session")
        self.assertEqual(response.status_code, 200)
        return client

    def status(self, client):
        return client.get("/api/v1/simulation/status").json()

    def post(self, client, action, speed=60):
        return client.post(f"/api/v1/simulation/{action}", json={"speed_multiplier": speed} if action == "start" else None)

    def wait_for(self, predicate):
        deadline = time.monotonic() + 6
        while time.monotonic() < deadline:
            value = predicate()
            if value:
                return value
            time.sleep(0.01)
        self.fail("Timed out waiting for demo progress")

    def test_secure_cookie_bootstrap_and_csrf(self):
        response = self.client.get("/api/v1/session", headers={"X-CGM-Demo": "1"})
        cookie = response.headers["set-cookie"]
        for attribute in ("HttpOnly", "Secure", "SameSite=strict", "Path=/"):
            self.assertIn(attribute, cookie)
        self.assertEqual(len(self.client.cookies[COOKIE_NAME]), 43)
        self.assertEqual(self.client.post("/api/v1/simulation/start", json={}).status_code, 403)
        for origin in ("https://evil.test", "null"):
            self.assertEqual(self.client.post("/api/v1/simulation/start", json={}, headers={"X-CGM-Demo": "1", "Origin": origin}).status_code, 403)
        self.assertEqual(self.client.get("/api/v1/session", headers={"X-CGM-Demo": "1", "Sec-Fetch-Site": "cross-site"}).status_code, 403)
        self.assertEqual(self.client.options("/api/v1/simulation/start", headers={"Origin": "https://evil.test", "Access-Control-Request-Headers": "X-CGM-Demo"}).headers.get("access-control-allow-origin"), None)
        self.assertEqual(self.client.get("/api/v1/notifications").headers["cache-control"], "no-store")

    def test_two_visitors_control_and_data_isolation(self):
        a, b = self.visitor(), self.visitor()
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(lambda c: self.post(c, "start", 3600), (a, b)))
        self.assertTrue(all(r.status_code == 200 for r in results))
        ra, rb = [r.json()["run_id"] for r in results]
        self.assertNotEqual(ra, rb)
        self.post(a, "pause").raise_for_status()
        self.assertEqual(self.status(a)["status"], "paused")
        self.assertEqual(self.status(b)["status"], "running")
        self.post(a, "resume").raise_for_status()
        self.assertEqual(self.status(a)["run_id"], ra)
        self.wait_for(lambda: a.get("/api/v1/notifications").json())
        notifications = a.get("/api/v1/notifications").json()
        notification_id = notifications[0]["id"]
        for endpoint in ("readings", "events", "notifications"):
            self.assertEqual(b.get(f"/api/v1/{endpoint}?run_id={ra}").json(), [])
            self.assertTrue(all(row["run_id"] == rb for row in b.get(f"/api/v1/{endpoint}").json()))
        self.assertEqual(b.post(f"/api/v1/notifications/{notification_id}/acknowledge").status_code, 404)
        ack = a.post(f"/api/v1/notifications/{notification_id}/acknowledge")
        self.assertEqual(ack.status_code, 200)
        self.assertEqual(ack.json()["status"], "read")
        self.assertEqual(a.post(f"/api/v1/notifications/{notification_id}/acknowledge").json()["acknowledged_at"], ack.json()["acknowledged_at"])
        self.assertEqual(b.post("/api/v1/readings", json={"run_id": ra}).status_code, 403)
        self.assertEqual({r["run_id"] for r in b.get("/api/v1/simulation/runs").json()}, {rb})
        # Supplying a foreign run_id cannot redirect the current controller.
        stop = b.post(f"/api/v1/simulation/stop?run_id={ra}")
        self.assertEqual(stop.json()["run_id"], rb)
        self.assertEqual(self.status(a)["status"], "running")
        self.post(a, "stop").raise_for_status()
        manager = self.app.state.demo_sessions
        manager.sessions[manager.owner_id(a.cookies[COOKIE_NAME])].last_start = float("-inf")
        new = self.post(a, "start")
        self.assertEqual(new.status_code, 200)
        self.assertNotEqual(new.json()["run_id"], ra)

    def test_refresh_and_backend_restart_restore_owner_and_ack(self):
        a = self.visitor()
        run_id = self.post(a, "start", 3600).json()["run_id"]
        notification = self.wait_for(lambda: a.get("/api/v1/notifications").json())[0]
        self.post(a, "pause").raise_for_status()
        a.post(f'/api/v1/notifications/{notification["id"]}/acknowledge').raise_for_status()
        refreshed = TestClient(self.app, base_url="https://demo.test", cookies=dict(a.cookies), headers={"X-CGM-Demo": "1"})
        self.clients.append(refreshed)
        self.assertEqual(self.status(refreshed)["run_id"], run_id)
        self.assertEqual(refreshed.get("/api/v1/notifications").json()[0]["status"], "read")
        cookies = dict(a.cookies)
        self.client.__exit__(None, None, None)
        self.database.dispose()
        self.open_app(cookies)
        self.client.headers["X-CGM-Demo"] = "1"
        self.client.get("/api/v1/session").raise_for_status()
        self.assertEqual(self.status(self.client)["run_id"], run_id)
        self.assertEqual(self.status(self.client)["status"], "stopped")
        self.assertEqual(self.client.get("/api/v1/notifications").json()[0]["status"], "read")
        self.post(self.client, "resume").raise_for_status()

    def test_lost_database_rotates_old_cookie_and_starts_empty(self):
        a = self.visitor()
        self.post(a, "start").raise_for_status()
        cookies = dict(a.cookies)
        self.client.__exit__(None, None, None)
        self.database.dispose()
        self.path.unlink()  # Only this test's temporary database.
        self.open_app(cookies)
        self.client.headers["X-CGM-Demo"] = "1"
        self.assertEqual(self.client.get("/api/v1/simulation/status").status_code, 401)
        self.client.get("/api/v1/session").raise_for_status()
        self.assertNotEqual(self.client.cookies[COOKIE_NAME], cookies[COOKIE_NAME])
        self.assertEqual(self.status(self.client)["status"], "idle")
        self.assertEqual(self.client.get("/api/v1/readings").json(), [])
        self.post(self.client, "start").raise_for_status()

    def test_active_limit_rate_limit_and_history_bound(self):
        a, b = self.visitor(), self.visitor()
        with patch("app.services.demo_sessions.MAX_ACTIVE", 1):
            self.post(a, "start").raise_for_status()
            self.post(a, "pause").raise_for_status()
            self.assertEqual(self.post(b, "start").status_code, 429)
            self.post(a, "stop").raise_for_status()
            self.assertEqual(self.post(a, "start").status_code, 429)
            self.post(b, "start").raise_for_status()
        entry = self.app.state.demo_sessions.sessions[self.app.state.demo_sessions.owner_id(a.cookies[COOKIE_NAME])]
        for _ in range(MAX_RUNS + 1):
            entry.last_start = float("-inf")
            self.post(a, "start").raise_for_status()
            self.post(a, "stop").raise_for_status()
        self.assertEqual(len(a.get("/api/v1/simulation/runs").json()), MAX_RUNS)
        with self.database.session() as db:
            self.assertEqual(db.scalar(select(func.count()).select_from(SimulationRun).where(SimulationRun.user_id == entry.processor.owner_id)), MAX_RUNS)

    def test_expiry_and_capacity_are_bounded(self):
        a = self.visitor()
        manager = self.app.state.demo_sessions
        entry = manager.sessions[manager.owner_id(a.cookies[COOKIE_NAME])]
        self.post(a, "start").raise_for_status()
        entry.touched -= SESSION_IDLE_SECONDS + 1
        self.assertEqual(a.get("/api/v1/simulation/status").status_code, 401)
        with self.database.session() as db:
            self.assertIsNone(db.get(User, entry.processor.owner_id))
        with patch("app.services.demo_sessions.MAX_SESSIONS", 1):
            self.visitor()
            self.assertEqual(self.client.get("/api/v1/session", headers={"X-CGM-Demo": "1"}).status_code, 503)

    def test_synthetic_scenario_ack_worsening_and_recovery(self):
        a = self.visitor()
        manager = self.app.state.demo_sessions
        entry = manager.sessions[manager.owner_id(a.cookies[COOKIE_NAME])]
        rows = load_readings(Path(__file__).resolve().parents[1] / "demo" / "synthetic.csv", timezone_name="UTC")
        self.assertLessEqual((rows[-1].measured_at - rows[0].measured_at).total_seconds() / 60, 180)
        def feed(start, end):
            for i in range(start, end):
                entry.processor.add_and_analyze(Reading(run_id="synthetic-test", source_index=i, measured_at=rows[i].measured_at.isoformat(), glucose_mmol_l=rows[i].glucose_mmol_l))
        feed(0, 9)
        first = a.get("/api/v1/notifications").json()
        self.assertEqual(len(first), 1)
        a.post(f'/api/v1/notifications/{first[0]["id"]}/acknowledge').raise_for_status()
        feed(9, 14)
        self.assertEqual(len(a.get("/api/v1/notifications").json()), 2)
        feed(14, 24)
        self.assertEqual(len(a.get("/api/v1/notifications").json()), 2)
        feed(24, len(rows))
        self.assertGreaterEqual(len(a.get("/api/v1/notifications").json()), 3)

    def test_static_frontend_and_api_404_are_distinct(self):
        response = self.client.get("/")
        if not (Path(__file__).resolve().parents[1] / "frontend" / "dist" / "index.html").exists():
            self.skipTest("Run npm run build for static delivery verification")
        self.assertEqual(response.status_code, 200)
        self.assertIn('id="root"', response.text)
        self.assertEqual(self.client.get("/health").json(), {"status": "ok"})
        a = self.visitor()
        self.assertEqual(a.get("/api/v1/missing").status_code, 404)
        for path in ("/data/kristina/cleaned_glucose_data.csv", "/demo/synthetic.csv", "/docs", "/openapi.json", "/assets/../../backend/app/main.py"):
            self.assertEqual(a.get(path).status_code, 404)
