import os
import tempfile

_d = tempfile.mkdtemp()
os.environ.update(DATABASE_URL=f"sqlite:///{_d}/t.db", UPLOADS=f"{_d}/up", SECRET_KEY="teste")

import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402
from sqlmodel import SQLModel  # noqa: E402

from app.db import engine  # noqa: E402
from app.main import app  # noqa: E402

SQLModel.metadata.create_all(engine)


@pytest.fixture()
def c():
    SQLModel.metadata.drop_all(engine)
    SQLModel.metadata.create_all(engine)
    with TestClient(app, follow_redirects=False) as cli:  # `with` dispara o seed da impressora
        yield cli
