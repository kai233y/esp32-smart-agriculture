"""Generate local credentials; refuses to overwrite an existing .env."""
from pathlib import Path
import secrets

target = Path(__file__).resolve().parents[1] / ".env"
with target.open("x") as f:
    for name in ("ADMIN_TOKEN", "DEVICE_TOKEN", "CAMERA_TOKEN"):
        f.write(f"{name}={secrets.token_urlsafe(32)}\n")
target.chmod(0o600)
print(f"已生成 {target}。请本地查看并复制对应令牌到固件配置，勿公开此文件。")
