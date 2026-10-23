"""Single-zone agriculture gateway. Run exactly one uvicorn worker."""
import asyncio
import hmac
import io
import json
import logging
import math
import os
from pathlib import Path
import secrets
import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from typing import Literal

from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from PIL import Image, UnidentifiedImageError
from pydantic import BaseModel, ConfigDict, Field, model_validator

ROOT = Path(__file__).resolve().parent
DATA = Path(os.getenv("DATA_DIR", str(ROOT / "data")))
ADMIN_TOKEN = os.getenv("ADMIN_TOKEN", "")
DEVICE_TOKEN = os.getenv("DEVICE_TOKEN", "")
CAMERA_TOKEN = os.getenv("CAMERA_TOKEN", "")
DEVICE_ID = "farm-01"
CAMERA_ID = "cam-01"
MAX_IMAGE = 2 * 1024 * 1024
Image.MAX_IMAGE_PIXELS = 8_000_000
model = None
model_state = "unavailable"
infer_lock = threading.Lock()
upload_lock = asyncio.Lock()
log = logging.getLogger("farm")


def db():
    c = sqlite3.connect(DATA / "farm.sqlite3", timeout=10)
    c.row_factory = sqlite3.Row
    return c


class Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", allow_inf_nan=False)


class Policy(Strict):
    auto_enabled: bool = False
    soil_on: float = Field(30, ge=0, le=95)
    soil_off: float = Field(45, ge=5, le=100)
    temp_on: float = Field(30, ge=5, le=60)
    temp_off: float = Field(27, ge=0, le=55)
    lux_on: float = Field(2000, ge=0, le=100000)
    lux_off: float = Field(4000, ge=1, le=150000)
    pump_max_s: int = Field(15, ge=1, le=60)
    pump_cooldown_s: int = Field(300, ge=30, le=3600)

    @model_validator(mode="after")
    def hysteresis(self):
        if not (self.soil_on < self.soil_off and self.temp_off < self.temp_on
                and self.lux_on < self.lux_off):
            raise ValueError("阈值需要形成回差：soil_on < soil_off；temp_off < temp_on；lux_on < lux_off")
        return self


class Telemetry(Strict):
    device_id: Literal["farm-01"]
    uptime_s: int = Field(ge=0)
    temperature: float | None = Field(None, ge=-40, le=85)
    humidity: float | None = Field(None, ge=0, le=100)
    soil_pct: float | None = Field(None, ge=0, le=100)
    soil_raw: int = Field(ge=0, le=4095)
    lux: float | None = Field(None, ge=0, le=200000)
    tank_ok: bool
    pump: bool
    fan: bool
    light: bool
    pump_cooling: bool
    auto_enabled: bool
    policy_revision: int = Field(ge=0)
    faults: list[str] = Field(default_factory=list, max_length=12)


class Command(Strict):
    target: Literal["pump", "fan", "light"]
    mode: Literal["auto", "on", "off"]
    ttl_s: int = Field(30, ge=1, le=300)


def check_key(actual, expected):
    if not expected or not actual or not hmac.compare_digest(actual, expected):
        raise HTTPException(401, "令牌无效")


def admin(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key, ADMIN_TOKEN)


def device(x_api_key: str | None = Header(default=None)):
    check_key(x_api_key, DEVICE_TOKEN)


def camera_or_admin(x_api_key: str | None = Header(default=None)):
    if x_api_key and ADMIN_TOKEN and hmac.compare_digest(x_api_key, ADMIN_TOKEN):
        return
    check_key(x_api_key, CAMERA_TOKEN)


def init():
    DATA.mkdir(parents=True, exist_ok=True)
    (DATA / "images").mkdir(exist_ok=True)
    with db() as c:
        c.execute("PRAGMA journal_mode=WAL")
        c.executescript("""
        CREATE TABLE IF NOT EXISTS policy (id INTEGER PRIMARY KEY CHECK(id=1), revision INTEGER, body TEXT);
        CREATE TABLE IF NOT EXISTS telemetry (id INTEGER PRIMARY KEY, ts REAL, body TEXT);
        CREATE TABLE IF NOT EXISTS commands (target TEXT PRIMARY KEY, mode TEXT, expires REAL);
        CREATE TABLE IF NOT EXISTS images (id TEXT PRIMARY KEY, ts REAL, status TEXT, body TEXT);
        CREATE TABLE IF NOT EXISTS audit (id INTEGER PRIMARY KEY, ts REAL, action TEXT, body TEXT);
        """)
        c.execute("INSERT OR IGNORE INTO policy VALUES(1,1,?)", (Policy().model_dump_json(),))


@asynccontextmanager
async def lifespan(app):
    global model, model_state
    keys = [ADMIN_TOKEN, DEVICE_TOKEN, CAMERA_TOKEN]
    if any(len(k) < 24 for k in keys) or len(set(keys)) != 3:
        raise RuntimeError("请设置三个不同且至少24字符的 ADMIN_TOKEN / DEVICE_TOKEN / CAMERA_TOKEN")
    init()
    weights = os.getenv("MODEL_PATH", "")
    if weights:
        try:
            if not Path(weights).is_file():
                raise FileNotFoundError(weights)
            from ultralytics import YOLO
            model = YOLO(weights, task="detect")
            if model.task != "detect":
                raise ValueError("需要目标检测模型")
            model_state = "ready"
        except Exception:
            model = None
            model_state = "error"
            log.exception("模型加载失败；环境感知与控制服务继续运行")
    yield


app = FastAPI(title="ESP32 智慧农业", lifespan=lifespan)
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")


@app.get("/")
def index():
    return FileResponse(ROOT / "static" / "index.html")


@app.get("/health")
def health():
    return {"ok": True, "model": model_state}


@app.post("/api/telemetry", dependencies=[Depends(device)])
def telemetry(t: Telemetry):
    now = time.time()
    with db() as c:
        c.execute("INSERT INTO telemetry(ts,body) VALUES(?,?)", (now, t.model_dump_json()))
        # Bounded history: latest 10000 samples (~28 h at 10 s). Export first for long-term storage.
        c.execute("DELETE FROM telemetry WHERE id <= (SELECT MAX(id)-10000 FROM telemetry)")
    return {"ok": True, "server_ts": now}


def policy_snapshot(c):
    r = c.execute("SELECT revision,body FROM policy WHERE id=1").fetchone()
    return {"revision": r["revision"], "policy": json.loads(r["body"])}


@app.get("/api/control", dependencies=[Depends(device)])
def control():
    now = time.time()
    with db() as c:
        result = policy_snapshot(c)
        result["manual"] = {
            r["target"]: {"mode": r["mode"], "remaining_s": max(0, math.floor(r["expires"] - now))}
            for r in c.execute("SELECT * FROM commands WHERE expires > ?", (now,))
        }
    return result


@app.put("/api/policy", dependencies=[Depends(admin)])
def update_policy(p: Policy):
    with db() as c:
        c.execute("UPDATE policy SET revision=revision+1,body=? WHERE id=1", (p.model_dump_json(),))
        c.execute("INSERT INTO audit(ts,action,body) VALUES(?,?,?)", (time.time(), "policy", p.model_dump_json()))
        return policy_snapshot(c)


@app.post("/api/commands", dependencies=[Depends(admin)])
def command(cmd: Command):
    with db() as c:
        if cmd.mode == "auto":
            c.execute("DELETE FROM commands WHERE target=?", (cmd.target,))
        else:
            c.execute("INSERT OR REPLACE INTO commands VALUES(?,?,?)", (cmd.target, cmd.mode, time.time() + cmd.ttl_s))
        c.execute("INSERT INTO audit(ts,action,body) VALUES(?,?,?)", (time.time(), "command", cmd.model_dump_json()))
    return {"ok": True, "note": "仅保存期望状态；以设备遥测确认执行，安全联锁始终优先"}


@app.get("/api/dashboard", dependencies=[Depends(admin)])
def dashboard():
    with db() as c:
        r = c.execute("SELECT ts,body FROM telemetry ORDER BY id DESC LIMIT 1").fetchone()
        latest = {"ts": r["ts"], **json.loads(r["body"])} if r else None
        history = [{"ts": r["ts"], **json.loads(r["body"])} for r in
                   c.execute("SELECT ts,body FROM telemetry ORDER BY id DESC LIMIT 120")][::-1]
        images = [{"id": r["id"], "ts": r["ts"], "status": r["status"], **json.loads(r["body"])}
                  for r in c.execute("SELECT * FROM images ORDER BY ts DESC LIMIT 12")]
        audits = [dict(r) for r in c.execute("SELECT * FROM audit ORDER BY id DESC LIMIT 20")]
        return {**policy_snapshot(c), "latest": latest, "history": history,
                "online": bool(latest and time.time() - latest["ts"] < 35),
                "images": images, "model": model_state, "audit": audits}


def analyze_image(image: Image.Image):
    if model is None:
        return {"status": "model_unavailable", "detections": [],
                "message": "尚未加载农业专用模型，图片已保存，未作出病虫害判断"}
    try:
        with infer_lock:
            result = model.predict(image, conf=float(os.getenv("MODEL_CONF", ".4")),
                                   imgsz=640, verbose=False, device="cpu")[0]
        detections = []
        for b in result.boxes:
            idx = int(b.cls.item())
            detections.append({"class_id": idx, "label": str(result.names[idx]),
                               "confidence": round(float(b.conf.item()), 4),
                               "xyxy": [round(float(v), 1) for v in b.xyxy[0].tolist()]})
        return {"status": "detected" if detections else "no_detection", "detections": detections,
                "message": "识别结果仅供人工复核；未检出不代表健康"}
    except Exception:
        log.exception("推理失败")
        return {"status": "inference_error", "detections": [], "message": "推理失败，图片已保存"}


def process_image(raw):
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.format not in {"JPEG", "PNG"}:
                raise ValueError("只接受 JPEG/PNG")
            if source.width * source.height > 8_000_000:
                raise ValueError("像素超过800万")
            source.load()
            img = source.convert("RGB")
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError) as e:
        raise HTTPException(400, "图片无效或分辨率过大") from e
    img.thumbnail((1600, 1600))
    image_id = secrets.token_hex(16)
    path = DATA / "images" / f"{image_id}.jpg"
    img.save(path, "JPEG", quality=88)
    result = analyze_image(img)
    result.update(width=img.width, height=img.height)
    with db() as c:
        c.execute("INSERT INTO images VALUES(?,?,?,?)", (image_id, time.time(), result["status"], json.dumps(result)))
        stale = c.execute("SELECT id FROM images ORDER BY ts DESC LIMIT -1 OFFSET 200").fetchall()
        for r in stale:
            (DATA / "images" / f"{r['id']}.jpg").unlink(missing_ok=True)
            c.execute("DELETE FROM images WHERE id=?", (r["id"],))
    return {"id": image_id, **result}


@app.post("/api/images", dependencies=[Depends(camera_or_admin)])
async def upload(request: Request):
    if upload_lock.locked():
        raise HTTPException(429, "图片处理繁忙，请稍后重试")
    async with upload_lock:
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_IMAGE:
                raise HTTPException(413, "图片不能超过2 MiB")
        return await asyncio.to_thread(process_image, bytes(raw))


@app.get("/api/images/{image_id}", dependencies=[Depends(admin)])
def get_image(image_id: str):
    if len(image_id) != 32 or any(x not in "0123456789abcdef" for x in image_id):
        raise HTTPException(404)
    path = DATA / "images" / f"{image_id}.jpg"
    if not path.is_file():
        raise HTTPException(404)
    return FileResponse(path, media_type="image/jpeg", headers={"Cache-Control": "no-store"})
