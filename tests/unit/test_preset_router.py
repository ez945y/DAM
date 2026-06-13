"""HTTP-level tests for /api/system/presets via FastAPI TestClient."""

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from dam.services.routers.system import create_system_router


@pytest.fixture
def client(tmp_path, monkeypatch):
    """Build a FastAPI app with the system router pointed at a temp data dir
    so user-preset writes don't touch ./data."""
    monkeypatch.setenv("DAM_DATA_ROOT", str(tmp_path))
    app = FastAPI()
    app.include_router(create_system_router(control=None))
    return TestClient(app)


# ── GET ───────────────────────────────────────────────────────────────────────


def test_list_returns_bundled_seeds(client):
    resp = client.get("/api/system/presets")
    assert resp.status_code == 200
    names = [p["name"] for p in resp.json()["presets"]]
    assert "so101_follower" in names


# ── POST create / update ──────────────────────────────────────────────────────


def test_create_user_preset(client):
    body = {
        "name": "my_arm",
        "joint_names": ["j0", "j1"],
        "degrees_mode": False,
        "asset": {"type": "urdf", "path": "/abs/path/foo.urdf"},
        "solvers": {"arm": {"type": "pinocchio_kinematics"}},
        "action_layout": [{"name": "arm", "type": "joint_position"}],
    }
    resp = client.post("/api/system/presets", json=body)
    assert resp.status_code == 200, resp.text
    data = resp.json()
    assert data["name"] == "my_arm"
    assert data["joint_names"] == ["j0", "j1"]
    assert data["degrees_mode"] is False
    assert data["asset"]["type"] == "urdf"
    assert data["asset"]["path"] == "/abs/path/foo.urdf"
    assert "arm" in data["solvers"]
    assert data["action_layout"][0]["name"] == "arm"

    names = [p["name"] for p in client.get("/api/system/presets").json()["presets"]]
    assert "my_arm" in names


def test_update_overrides_bundled(client):
    """Upserting an existing bundled preset replaces it in the merged view."""
    body = {
        "name": "so101_follower",
        "joint_names": ["only_one"],
        "degrees_mode": False,
        "asset": None,
        "solvers": {},
    }
    resp = client.post("/api/system/presets", json=body)
    assert resp.status_code == 200
    so101 = next(
        p
        for p in client.get("/api/system/presets").json()["presets"]
        if p["name"] == "so101_follower"
    )
    assert so101["joint_names"] == ["only_one"]
    assert so101["degrees_mode"] is False


def test_rename_via_rename_from(client):
    """rename_from atomically removes the old key in one POST."""
    client.post(
        "/api/system/presets",
        json={
            "name": "old_name",
            "joint_names": ["j0"],
            "degrees_mode": True,
            "asset": None,
            "solvers": {},
        },
    )
    resp = client.post(
        "/api/system/presets",
        json={
            "name": "new_name",
            "joint_names": ["j0"],
            "degrees_mode": True,
            "asset": None,
            "solvers": {},
            "rename_from": "old_name",
        },
    )
    assert resp.status_code == 200
    names = [p["name"] for p in client.get("/api/system/presets").json()["presets"]]
    assert "new_name" in names
    assert "old_name" not in names


# ── POST validation errors ────────────────────────────────────────────────────


def test_create_rejects_empty_name(client):
    resp = client.post(
        "/api/system/presets",
        json={"name": "", "joint_names": ["j"], "degrees_mode": True, "asset": None},
    )
    assert resp.status_code == 400
    assert "name" in resp.json()["detail"].lower()


def test_create_rejects_empty_joints(client):
    resp = client.post(
        "/api/system/presets",
        json={"name": "foo", "joint_names": [], "degrees_mode": True, "asset": None},
    )
    assert resp.status_code == 400


def test_create_rejects_non_list_joints(client):
    resp = client.post(
        "/api/system/presets",
        json={"name": "foo", "joint_names": "not_a_list", "degrees_mode": True, "asset": None},
    )
    assert resp.status_code == 400


# ── DELETE ────────────────────────────────────────────────────────────────────


def test_delete_user_preset(client):
    client.post(
        "/api/system/presets",
        json={
            "name": "to_delete",
            "joint_names": ["j0"],
            "degrees_mode": True,
            "asset": None,
            "solvers": {},
        },
    )
    resp = client.delete("/api/system/presets/to_delete")
    assert resp.status_code == 200
    assert resp.json() == {"deleted": "to_delete"}
    names = [p["name"] for p in client.get("/api/system/presets").json()["presets"]]
    assert "to_delete" not in names


def test_delete_bundled_uses_tombstone(client):
    """Deleting a bundled preset hides it but never touches the in-git file."""
    resp = client.delete("/api/system/presets/so101_follower")
    assert resp.status_code == 200
    names = [p["name"] for p in client.get("/api/system/presets").json()["presets"]]
    assert "so101_follower" not in names

    # The bundled file is unchanged on disk.
    from dam.preset.registry import BUNDLED_PATH, _load_one

    assert "so101_follower" in _load_one(BUNDLED_PATH)


def test_delete_unknown_returns_404(client):
    resp = client.delete("/api/system/presets/never_existed")
    assert resp.status_code == 404


def test_delete_then_recreate(client):
    """After tombstoning a bundled preset, upserting the same name resurrects it."""
    client.delete("/api/system/presets/so101_follower")
    resp = client.post(
        "/api/system/presets",
        json={
            "name": "so101_follower",
            "joint_names": ["a", "b"],
            "degrees_mode": True,
            "asset": None,
            "solvers": {},
        },
    )
    assert resp.status_code == 200
    names = [p["name"] for p in client.get("/api/system/presets").json()["presets"]]
    assert "so101_follower" in names
