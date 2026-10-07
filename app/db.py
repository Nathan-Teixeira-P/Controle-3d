import os
from sqlmodel import Session, create_engine

engine = create_engine(os.environ.get("DATABASE_URL", "sqlite:///dev.db"))


def get_session():
    with Session(engine) as s:
        yield s
