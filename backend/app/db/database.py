from pathlib import Path
from typing import Any

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.engine import make_url
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from external.kirill_db import db as kirill_db


class Database:
    def __init__(self, url: str | None = None) -> None:
        self.url = url or kirill_db.DB_URL
        if url is None:
            self.engine = kirill_db.engine
            self.session_factory = kirill_db.Session
        else:
            parsed_url = make_url(self.url)
            connect_args: dict[str, Any] = {}
            engine_options: dict[str, Any] = {}
            if parsed_url.get_backend_name() == "sqlite":
                connect_args["check_same_thread"] = False
                if parsed_url.database and parsed_url.database != ":memory:":
                    Path(parsed_url.database).expanduser().resolve().parent.mkdir(
                        parents=True, exist_ok=True
                    )
                elif parsed_url.database == ":memory:":
                    engine_options["poolclass"] = StaticPool
            self.engine = kirill_db._configure_sqlite(
                create_engine(
                    self.url,
                    connect_args=connect_args,
                    **engine_options,
                )
            )
            self.session_factory = sessionmaker(
                bind=self.engine,
                expire_on_commit=False,
            )

    def initialize(self) -> None:
        kirill_db.Base.metadata.create_all(self.engine)
        if self.engine.dialect.name == "sqlite":
            timestamp_columns = {
                "glucose_readings": ("source_measured_at",),
                "glucose_events": (
                    "source_started_at",
                    "source_ended_at",
                    "source_measured_at",
                ),
            }
            with self.engine.begin() as connection:
                inspector = inspect(connection)
                for table_name, columns in timestamp_columns.items():
                    existing = {
                        column["name"]
                        for column in inspector.get_columns(table_name)
                    }
                    for column_name in columns:
                        if column_name not in existing:
                            connection.execute(
                                text(
                                    f"ALTER TABLE {table_name} "
                                    f"ADD COLUMN {column_name} TEXT"
                                )
                            )

    def session(self) -> Session:
        return self.session_factory()

    def dispose(self) -> None:
        self.engine.dispose()


database = Database()
