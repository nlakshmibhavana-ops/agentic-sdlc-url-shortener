import pytest
from fastapi.testclient import TestClient

from linkly.main import create_app


@pytest.fixture
def db_path(tmp_path):
    return str(tmp_path / "linkly.db")


@pytest.fixture
def client(db_path):
    return TestClient(create_app(db_path), follow_redirects=False)
