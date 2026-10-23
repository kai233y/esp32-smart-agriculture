import importlib
import io
import sys
from pathlib import Path
import pytest
from fastapi.testclient import TestClient
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))


@pytest.fixture
def env(tmp_path, monkeypatch):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    for name, char in [("ADMIN_TOKEN", "a"), ("DEVICE_TOKEN", "d"), ("CAMERA_TOKEN", "c")]:
        monkeypatch.setenv(name, char * 32)
    monkeypatch.delenv("MODEL_PATH", raising=False)
    from server import app
    importlib.reload(app)
    with TestClient(app.app) as client:
        yield client, app


def auth(role="a"):
    return {"X-API-Key": role * 32}


def test_auth_and_role_separation(env):
    c, _ = env
    assert c.get("/api/dashboard").status_code == 401
    assert c.get("/api/dashboard", headers=auth("d")).status_code == 401
    assert c.get("/api/control", headers=auth("c")).status_code == 401
    assert c.get("/api/dashboard", headers=auth()).status_code == 200
    assert c.get("/").status_code == 200
    assert c.get("/static/app.js").status_code == 200


def test_telemetry_and_invalid_values(env):
    c, _ = env
    payload = dict(device_id="farm-01", uptime_s=10, temperature=None, humidity=70,
                   soil_pct=22, soil_raw=2500, lux=100, tank_ok=False, pump=False, fan=True,
                   light=False, pump_cooling=True, auto_enabled=False, policy_revision=1,
                   faults=["tank_low_or_wire_open"])
    assert c.post("/api/telemetry", headers=auth("d"), json=payload).status_code == 200
    s = c.get("/api/dashboard", headers=auth()).json()
    assert s["online"] and s["latest"]["temperature"] is None
    assert s["latest"]["faults"] == ["tank_low_or_wire_open"]
    payload["soil_pct"] = 120
    assert c.post("/api/telemetry", headers=auth("d"), json=payload).status_code == 422


def test_policy_validation_and_revision(env):
    c, app = env
    p = app.Policy().model_dump()
    assert p["auto_enabled"] is False
    p["soil_off"] = 20
    assert c.put("/api/policy", headers=auth(), json=p).status_code == 422
    p["soil_off"] = 50
    r = c.put("/api/policy", headers=auth(), json=p).json()
    assert r["revision"] == 2
    assert c.get("/api/control", headers=auth("d")).json()["policy"]["soil_off"] == 50


def test_manual_expiry_and_restore(env, monkeypatch):
    c, app = env
    monkeypatch.setattr(app.time, "time", lambda: 1000.0)
    cmd = dict(target="pump", mode="on", ttl_s=15)
    assert c.post("/api/commands", headers=auth(), json=cmd).status_code == 200
    assert c.get("/api/control", headers=auth("d")).json()["manual"]["pump"]["remaining_s"] == 15
    monkeypatch.setattr(app.time, "time", lambda: 1015.0)
    assert not c.get("/api/control", headers=auth("d")).json()["manual"]
    c.post("/api/commands", headers=auth(), json=cmd)
    cmd["mode"] = "auto"
    c.post("/api/commands", headers=auth(), json=cmd)
    assert not c.get("/api/control", headers=auth("d")).json()["manual"]


def test_images_no_fake_inference(env):
    c, _ = env
    raw = io.BytesIO()
    Image.new("RGB", (80, 60), "green").save(raw, format="JPEG")
    r = c.post("/api/images", headers=auth("c"), content=raw.getvalue())
    assert r.status_code == 200
    data = r.json()
    assert data["status"] == "model_unavailable" and not data["detections"]
    assert c.get("/api/images/" + data["id"], headers=auth()).status_code == 200
    assert c.get("/api/images/" + data["id"], headers=auth("c")).status_code == 401
    assert c.post("/api/images", headers=auth("c"), content=b"not an image").status_code == 400
    assert c.post("/api/images", headers=auth("c"), content=b"x" * (2 * 1024 * 1024 + 1)).status_code == 413


def test_offline_state(env, monkeypatch):
    c, app = env
    assert c.get("/api/dashboard", headers=auth()).json()["online"] is False
    with app.db() as conn:
        conn.execute("INSERT INTO telemetry(ts,body) VALUES(?,?)", (1, '{}'))
    assert c.get("/api/dashboard", headers=auth()).json()["online"] is False
