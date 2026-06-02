from __future__ import annotations

import hashlib
import os
import platform
import socket
import subprocess
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

import requests

from .settings import AppConfig


@dataclass
class LicenseStatus:
    ok: bool
    message: str
    expires_at: str = ""
    customer: str = ""


def get_machine_code() -> str:
    raw_parts = [
        platform.node(),
        platform.system(),
        platform.machine(),
        str(uuid.getnode()),
        _windows_uuid(),
    ]
    raw = "|".join(part for part in raw_parts if part)
    digest = hashlib.sha256(raw.encode("utf-8", errors="ignore")).hexdigest().upper()
    return "-".join([digest[:8], digest[8:16], digest[16:24], digest[24:32]])


def verify_license(config: AppConfig, timeout: int = 12) -> LicenseStatus:
    key = (config.license_key or "").strip()
    if not key:
        return LicenseStatus(False, "请先填写卡密")

    server = (config.license_server_url or "").strip().rstrip("/")
    if not server:
        return LicenseStatus(False, "授权服务器地址为空")

    try:
        session = requests.Session()
        session.trust_env = False
        response = session.post(
            f"{server}/api/verify",
            json={
                "license_key": key,
                "machine_code": get_machine_code(),
                "app": "ReCreate AI",
            },
            timeout=timeout,
        )
        data = response.json()
    except Exception as exc:
        return LicenseStatus(False, f"授权服务器连接失败: {exc}")

    if response.status_code != 200:
        return LicenseStatus(False, data.get("message") or f"授权服务器返回 HTTP {response.status_code}")

    ok = bool(data.get("ok"))
    message = data.get("message") or ("授权有效" if ok else "授权失败")
    expires_at = data.get("expires_at") or ""
    customer = data.get("customer") or ""
    return LicenseStatus(ok, message, expires_at, customer)


def require_valid_license(config: AppConfig) -> LicenseStatus:
    status = verify_license(config)
    if not status.ok:
        raise RuntimeError(status.message)
    return status


def _windows_uuid() -> str:
    if os.name != "nt":
        return ""
    try:
        result = subprocess.run(
            ["wmic", "csproduct", "get", "uuid"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        lines = [line.strip() for line in result.stdout.splitlines() if line.strip()]
        if len(lines) >= 2 and lines[1].lower() != "uuid":
            return lines[1]
    except Exception:
        pass
    try:
        return socket.gethostname()
    except Exception:
        return ""


def parse_server_time(value: str) -> datetime | None:
    if not value:
        return None
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00")).astimezone(timezone.utc)
    except Exception:
        return None
