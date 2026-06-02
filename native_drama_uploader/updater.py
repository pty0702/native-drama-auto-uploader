from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import tempfile
import zipfile
from dataclasses import dataclass
from pathlib import Path

import requests

from . import __version__
from .settings import AppConfig, PROJECT_DIR
from .license_client import get_machine_code


@dataclass
class UpdateInfo:
    available: bool
    current_version: str
    latest_version: str = ""
    notes: str = ""
    url: str = ""
    sha256: str = ""
    force: bool = False


def check_update(config: AppConfig, timeout: int = 10) -> UpdateInfo:
    server = (config.update_server_url or config.license_server_url or "").strip().rstrip("/")
    if not server:
        return UpdateInfo(False, __version__)

    session = requests.Session()
    session.trust_env = False
    payload = {
        "license_key": (config.license_key or "").strip(),
        "machine_code": get_machine_code(),
        "current_version": __version__,
        "app": "ReCreate AI",
    }
    response = session.post(f"{server}/api/version", json=payload, timeout=timeout)
    response.raise_for_status()
    data = response.json()
    latest = str(data.get("version") or "").strip()
    if not latest:
        return UpdateInfo(False, __version__)
    return UpdateInfo(
        available=_version_tuple(latest) > _version_tuple(__version__),
        current_version=__version__,
        latest_version=latest,
        notes=str(data.get("notes") or ""),
        url=str(data.get("url") or ""),
        sha256=str(data.get("sha256") or ""),
        force=bool(data.get("force")),
    )


def download_update(info: UpdateInfo, progress_cb=None) -> Path:
    if not info.url:
        raise RuntimeError("更新包下载地址为空")
    tmp_dir = Path(tempfile.gettempdir()) / "ReCreateAI_Update"
    tmp_dir.mkdir(parents=True, exist_ok=True)
    zip_path = tmp_dir / "ReCreate_AI_update.zip"
    if zip_path.exists():
        zip_path.unlink()

    session = requests.Session()
    session.trust_env = False
    with session.get(info.url, stream=True, timeout=120) as response:
        response.raise_for_status()
        total = int(response.headers.get("Content-Length", "0") or 0)
        done = 0
        with zip_path.open("wb") as f:
            for chunk in response.iter_content(chunk_size=1024 * 512):
                if not chunk:
                    continue
                f.write(chunk)
                done += len(chunk)
                if progress_cb and total:
                    progress_cb(min(100, int(done * 100 / total)))

    if info.sha256:
        digest = hashlib.sha256(zip_path.read_bytes()).hexdigest().lower()
        if digest != info.sha256.lower():
            raise RuntimeError("更新包校验失败，请重新下载")
    return zip_path


def schedule_update_and_exit(zip_path: Path) -> None:
    app_dir = PROJECT_DIR
    exe_path = Path(sys.executable if getattr(sys, "frozen", False) else app_dir / "ReCreate AI.exe")
    script_path = Path(tempfile.gettempdir()) / "ReCreateAI_Update" / "apply_update.ps1"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    script = f"""
$ErrorActionPreference = 'Stop'
Start-Sleep -Seconds 2
$zip = '{_ps_escape(str(zip_path))}'
$appDir = '{_ps_escape(str(app_dir))}'
$extract = Join-Path $env:TEMP 'ReCreateAI_Update\\extract'
if (Test-Path $extract) {{ Remove-Item -LiteralPath $extract -Recurse -Force }}
New-Item -ItemType Directory -Path $extract -Force | Out-Null
Expand-Archive -LiteralPath $zip -DestinationPath $extract -Force
$inner = Join-Path $extract 'ReCreate AI'
if (-not (Test-Path $inner)) {{ throw '更新包结构错误：缺少 ReCreate AI 目录' }}
Get-ChildItem -LiteralPath $inner -Force | ForEach-Object {{
    $target = Join-Path $appDir $_.Name
    if (Test-Path -LiteralPath $target) {{
        Remove-Item -LiteralPath $target -Recurse -Force
    }}
    Move-Item -LiteralPath $_.FullName -Destination $target -Force
}}
Start-Process -FilePath '{_ps_escape(str(exe_path))}'
"""
    script_path.write_text(script, encoding="utf-8")
    subprocess.Popen(
        [
            "powershell",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(script_path),
        ],
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    QApplication = _qt_app()
    if QApplication:
        QApplication.quit()
    else:
        os._exit(0)


def _version_tuple(value: str) -> tuple[int, ...]:
    parts = []
    for part in value.replace("-", ".").split("."):
        try:
            parts.append(int(part))
        except ValueError:
            parts.append(0)
    return tuple(parts)


def _ps_escape(value: str) -> str:
    return value.replace("'", "''")


def _qt_app():
    try:
        from PyQt5.QtWidgets import QApplication

        return QApplication.instance()
    except Exception:
        return None
