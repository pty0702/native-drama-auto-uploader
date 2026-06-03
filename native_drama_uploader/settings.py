from __future__ import annotations

import json
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


if getattr(sys, "frozen", False):
    PROJECT_DIR = Path(sys.executable).resolve().parent
else:
    PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_DIR = PROJECT_DIR / "db"
DEBUG_DIR = PROJECT_DIR / "debug"
COOKIES_DIR = PROJECT_DIR / "cookies" / "tencent_uploader"
CONFIG_PATH = DB_DIR / "config.json"
QUEUE_PATH = DB_DIR / "upload_queue.json"
DEFAULT_ACCOUNT_STATE_PATH = COOKIES_DIR / "account.json"
LEGACY_ACCOUNT_STATE_PATH = r"G:\python_file\social-auto-upload\cookies\tencent_uploader\account.json"

# 素材目录
SUCAI_DIR = PROJECT_DIR / "sucai"
DEFAULT_DOCX_TEMPLATE = SUCAI_DIR / "视频.docx"
DEFAULT_STAMP_IMAGE = SUCAI_DIR / "模板.jpg"

# 默认 API 地址只预填服务地址，不预填任何 API Key。
TEXT_API_BASE_URL = "https://ark.cn-beijing.volces.com/api/v3"
IMAGE_API_BASE_URL = "https://aheapi.com"
TEXT_MODEL = "doubao-seed-2-0-lite-260428"
IMAGE_MODEL = "gpt-image-2"
LICENSE_SERVER_URL = "http://124.220.63.163:8787"
UPDATE_SERVER_URL = LICENSE_SERVER_URL


def ensure_runtime_dirs() -> None:
    for path in (DB_DIR, DEBUG_DIR, COOKIES_DIR, SUCAI_DIR):
        path.mkdir(parents=True, exist_ok=True)


@dataclass
class AppConfig:
    watch_root: str = str(SUCAI_DIR)
    account_state_path: str = str(DEFAULT_ACCOUNT_STATE_PATH)
    default_company_name: str = "柯尔鸭有限公司"
    default_trial_episodes: int = 5
    default_production_cost: int = 1
    upload_interval_min: int = 10
    submit_after_upload: bool = True
    headless: bool = False
    browser_slow_mo_ms: int = 0
    upload_timeout_min: int = 60
    upload_poll_min: int = 2
    volc_api_key: str = ""
    api_base_url: str = TEXT_API_BASE_URL
    image_api_base_url: str = IMAGE_API_BASE_URL
    image_api_key: str = ""
    license_key: str = ""
    license_server_url: str = LICENSE_SERVER_URL
    update_server_url: str = UPDATE_SERVER_URL
    text_model: str = TEXT_MODEL
    image_model: str = IMAGE_MODEL
    output_dir: str = str(SUCAI_DIR)
    docx_template: str = str(DEFAULT_DOCX_TEMPLATE)
    stamp_image: str = str(DEFAULT_STAMP_IMAGE)
    image_target_width: int = 816
    image_target_height: int = 1086


def load_config() -> AppConfig:
    ensure_runtime_dirs()
    if not CONFIG_PATH.exists():
        save_config(AppConfig())
    try:
        data: dict[str, Any] = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except Exception:
        data = {}
    defaults = asdict(AppConfig())
    defaults.update({k: v for k, v in data.items() if k in defaults})
    config = AppConfig(**defaults)
    if config.account_state_path == LEGACY_ACCOUNT_STATE_PATH:
        config.account_state_path = str(DEFAULT_ACCOUNT_STATE_PATH)
        save_config(config)
    return config


def save_config(config: AppConfig) -> None:
    ensure_runtime_dirs()
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
