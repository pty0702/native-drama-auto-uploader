from __future__ import annotations

import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4


VIDEO_SUFFIXES = {".mp4", ".mov", ".m4v"}
IMAGE_SUFFIXES = {".jpg", ".jpeg", ".png", ".bmp"}
CONFIG_SUFFIXES = {".pdf", ".jpg", ".jpeg", ".png", ".bmp"}
PROOF_KEYWORDS = ("剪影", "截图", "证明", "制作", "合同")
CONFIG_KEYWORDS = ("配置表", "备案", "资质", "许可", "证明", "模版", "模板")


def now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def natural_key(path: Path) -> list[Any]:
    parts = re.split(r"(\d+)", path.name)
    return [int(part) if part.isdigit() else part.lower() for part in parts]


def same_path(left: Path, right: Path) -> bool:
    try:
        return left.resolve().samefile(right.resolve())
    except Exception:
        return str(left.resolve()).lower() == str(right.resolve()).lower()


def read_text_if_exists(path: Path) -> str:
    if not path.exists() or not path.is_file():
        return ""
    for encoding in ("utf-8", "utf-8-sig", "gbk"):
        try:
            return path.read_text(encoding=encoding).strip()
        except UnicodeDecodeError:
            continue
    return path.read_text(errors="ignore").strip()


def find_video_files(folder: Path) -> list[Path]:
    return sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in VIDEO_SUFFIXES],
        key=natural_key,
    )


def find_first_existing(folder: Path, names: tuple[str, ...], suffixes: set[str]) -> Path | None:
    for name in names:
        candidate = folder / name
        if candidate.exists() and candidate.is_file() and candidate.suffix.lower() in suffixes:
            return candidate
    return None


def find_cover(folder: Path) -> Path | None:
    direct = find_first_existing(
        folder,
        ("海报.jpg", "海报.jpeg", "海报.png", f"{folder.name}.jpg", f"{folder.name}.png"),
        IMAGE_SUFFIXES,
    )
    if direct:
        return direct
    images = sorted(
        [p for p in folder.iterdir() if p.is_file() and p.suffix.lower() in IMAGE_SUFFIXES],
        key=natural_key,
    )
    return images[0] if images else None


def find_config_file(folder: Path, cover_path: Path | None = None) -> Path | None:
    direct = find_first_existing(
        folder,
        ("模版.jpg", "模板.jpg", "模版.png", "模板.png", "配置表.pdf"),
        CONFIG_SUFFIXES,
    )
    if direct and (cover_path is None or not same_path(direct, cover_path)):
        return direct
    candidates = [
        p
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in CONFIG_SUFFIXES
        and (cover_path is None or not same_path(p, cover_path))
        and any(keyword in p.stem for keyword in CONFIG_KEYWORDS)
    ]
    return sorted(candidates, key=natural_key)[0] if candidates else None


def find_proof_images(folder: Path, cover_path: Path | None = None, config_path: Path | None = None) -> list[Path]:
    images = [
        p
        for p in folder.iterdir()
        if p.is_file()
        and p.suffix.lower() in IMAGE_SUFFIXES
        and (cover_path is None or not same_path(p, cover_path))
        and (config_path is None or not same_path(p, config_path))
    ]
    images = sorted(images, key=natural_key)
    matched = [p for p in images if any(keyword in p.stem for keyword in PROOF_KEYWORDS)]
    return (matched or images)[:4]


def find_description(folder: Path) -> str:
    for name in ("简介.txt", "剧情简介.txt", "summary.txt"):
        text = read_text_if_exists(folder / name)
        if text:
            return text[:100]
    return f"《{folder.name}》讲述一段情节紧凑、反转不断的短剧故事，适合连续观看。"


@dataclass
class NativeDramaTask:
    folder: str
    drama_name: str
    description: str
    video_files: list[str]
    cover_path: str
    template_path: str | None
    proof_images: list[str]
    company_name: str
    trial_episodes: int = 5
    production_cost: int = 1
    submit_after_upload: bool = True
    id: str = field(default_factory=lambda: uuid4().hex)
    status: str = "pending"
    created_at: str = field(default_factory=now_iso)
    updated_at: str = field(default_factory=now_iso)
    last_error: str = ""

    @property
    def episode_count(self) -> int:
        return len(self.video_files)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NativeDramaTask":
        return cls(**data)


def build_task_from_folder(
    folder: str | Path,
    company_name: str,
    trial_episodes: int = 5,
    production_cost: int = 1,
    submit_after_upload: bool = True,
) -> NativeDramaTask:
    path = Path(folder)
    if not path.exists() or not path.is_dir():
        raise FileNotFoundError(f"成品文件夹不存在: {path}")

    video_files = find_video_files(path)
    if not video_files:
        raise ValueError(f"成品文件夹里没有 mp4/mov/m4v 视频: {path}")

    cover_path = find_cover(path)
    if not cover_path:
        raise FileNotFoundError(f"成品文件夹里没有找到海报图片: {path}")

    config_path = find_config_file(path, cover_path)
    proof_images = find_proof_images(path, cover_path, config_path)

    return NativeDramaTask(
        folder=str(path),
        drama_name=path.name,
        description=find_description(path),
        video_files=[str(p) for p in video_files],
        cover_path=str(cover_path),
        template_path=str(config_path) if config_path else None,
        proof_images=[str(p) for p in proof_images],
        company_name=company_name,
        trial_episodes=trial_episodes,
        production_cost=production_cost,
        submit_after_upload=submit_after_upload,
    )
