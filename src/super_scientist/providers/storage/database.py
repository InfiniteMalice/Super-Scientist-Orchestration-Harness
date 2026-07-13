from __future__ import annotations

from pathlib import Path
from types import TracebackType

from alembic.config import Config
from sqlalchemy import Connection, Engine, create_engine

from alembic import command
from super_scientist.providers.storage.repositories import RepositorySet


def create_database_engine(url: str) -> Engine:
    return create_engine(url, future=True)


def upgrade_database(url: str) -> None:
    repository_root = Path(__file__).resolve().parents[4]
    packaged_migrations = Path(__file__).resolve().parents[2] / "_migrations"
    script_location = (
        packaged_migrations if packaged_migrations.exists() else repository_root / "alembic"
    )
    config = Config()
    config.set_main_option("script_location", str(script_location).replace("%", "%%"))
    config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
    command.upgrade(config, "head")


class DatabaseUnitOfWork:
    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self.connection: Connection | None = None

    def __enter__(self) -> DatabaseUnitOfWork:
        if self.connection is not None:
            raise RuntimeError("unit of work is already active")
        connection = self._engine.connect()
        self.connection = connection
        try:
            connection.exec_driver_sql("BEGIN IMMEDIATE")
        except BaseException:
            connection.close()
            self.connection = None
            raise
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        del exc, traceback
        connection = self.connection
        if connection is None:
            return
        try:
            if exc_type is None:
                try:
                    connection.commit()
                except BaseException:
                    connection.rollback()
                    raise
            else:
                connection.rollback()
        finally:
            connection.close()
            self.connection = None

    def repositories(self) -> RepositorySet:
        if self.connection is None or self.connection.closed:
            raise RuntimeError("unit of work is not active")
        return RepositorySet(self.connection)
