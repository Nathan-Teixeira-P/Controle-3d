from alembic import context
from sqlmodel import SQLModel

from app import models  # noqa: F401  (registra as tabelas)
from app.db import engine

target_metadata = SQLModel.metadata


def run():
    with engine.connect() as conn:
        context.configure(connection=conn, target_metadata=target_metadata, compare_type=True)
        with context.begin_transaction():
            context.run_migrations()


run()
