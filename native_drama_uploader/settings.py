from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any


PROJECT_DIR = Path(__file__).resolve().parents[1]
DB_DIR = PROJECT_DIR / "db"
DEBUG_DIR = PROJECT_DIR / "debug"
COOKIES_DIR = PROJECT_DIR / "cookies" / "tencent_uploader"
CONFIG_PATH = DB_DIR / "config.json"
QUEUE_PATH = DB_DIR / "upload_queue.json"
DEFAULT_ACCOUNT_STATE_PATH = COOKIES_DIR / "account.json"
LEGACY_ACCOUNT_STATE_PATH = r"G:\python_file\social-auto-upload\cookies\tencent_uploader\account.json"


@dataclass
class AppConfig:
    watch_root: str = r"G:\python_file\ai_manju5.27"
    account_state_path: str = str(DEFAULT_ACCOUNT_STATE_PATH)
    default_company_name: str = "柯尔鸭有限公司"
    default_trial_episodes: int = 5
    default_production_cost: int = 1
    upload_interval_min: int = 10
    submit_after_upload: bool = True
    headless: bool = False
    browser_slow_mo_ms: int = 0
    upload_timeout_min: int = 60
    upload_poll_min: int = 3
    volc_api_key: str = ""
    api_base_url: str = "https://ark.cn-beijing.volces.com/api/v3"
    text_model: str = "doubao-seed-2-0-lite-260428"
    image_model: str = "doubao-seedream-5-0-260128"
    output_dir: str = r"G:\python_file\ai_manju5.27"
    template_image: str = ""
    image_target_width: int = 816
    image_target_height: int = 1086


def load_config() -> AppConfig:
    DB_DIR.mkdir(parents=True, exist_ok=True)
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
    DB_DIR.mkdir(parents=True, exist_ok=True)
    COOKIES_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(asdict(config), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
