import pytest
from fastapi.testclient import TestClient

from metadata_service.api import create_app
from metadata_service.config import Settings


@pytest.fixture
def client(tmp_path):
    settings = Settings(data_dir=tmp_path).initialize()
    with TestClient(create_app(settings)) as client:
        yield client


@pytest.fixture
def store(client):
    return client.app.state.store
