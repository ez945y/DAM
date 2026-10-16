"""Library creation must never overwrite an existing user configuration."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dam.services.routers import stackfiles


@pytest.fixture
def library_client(tmp_path, monkeypatch):
    monkeypatch.setattr(stackfiles, "_root", lambda: tmp_path)
    app = FastAPI()
    app.include_router(stackfiles.create_stackfiles_router())
    with TestClient(app) as client:
        yield client


def test_create_duplicate_preserves_existing_yaml(library_client):
    original = "# User settings\nversion: '1'\n"
    first = library_client.post("/api/stackfiles/demo", json={"yaml": original})
    assert first.status_code == 201
    duplicate = library_client.post("/api/stackfiles/demo", json={"yaml": "replacement"})
    assert duplicate.status_code == 409
    assert library_client.get("/api/stackfiles/demo").text == original


def test_save_still_updates_existing_entry(library_client):
    library_client.post("/api/stackfiles/demo", json={"yaml": "original"})
    response = library_client.put("/api/stackfiles/demo", json={"yaml": "edited"})
    assert response.status_code == 200
    assert response.json() == {"name": "demo", "created": False}
    assert library_client.get("/api/stackfiles/demo").text == "edited"


@pytest.mark.parametrize("method", ["post", "put"])
@pytest.mark.parametrize("invalid", [None, 42, ["yaml"], {"version": 1}, "", " \n "])
def test_invalid_content_does_not_modify_entry(library_client, method, invalid):
    library_client.post("/api/stackfiles/demo", json={"yaml": "original"})
    response = getattr(library_client, method)("/api/stackfiles/demo", json={"yaml": invalid})
    assert response.status_code == 400
    assert library_client.get("/api/stackfiles/demo").text == "original"
