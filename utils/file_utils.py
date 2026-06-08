import os
import re


VIDEO_EXTS = {".mp4", ".mkv", ".avi", ".mov", ".flv", ".wmv"}
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}


def find_files(folder, ext_set):
    result = []
    for f in os.listdir(folder):
        ext = os.path.splitext(f)[1].lower()
        if ext in ext_set:
            result.append(os.path.join(folder, f))
    return sorted(result, key=_natural_file_key)


def find_txt(folder):
    for f in os.listdir(folder):
        if f.lower().endswith(".txt"):
            return os.path.join(folder, f)
    return None


def extract_episode_number(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    name = _normalize_digits(name)
    m = re.search(r"第\s*(\d+)\s*[集话話回]", name)
    if m:
        return int(m.group(1))
    m = re.search(r"(?:^|[^a-zA-Z])(?:EP|Ep|ep|E|e)\s*0*(\d+)(?:\D|$)", name)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", name)
    if m:
        return int(m.group(1))
    return 0


def _normalize_digits(value):
    return value.translate(str.maketrans("０１２３４５６７８９", "0123456789"))


def _natural_file_key(path):
    basename = os.path.basename(path)
    episode = extract_episode_number(basename)
    normalized = _normalize_digits(basename).lower()
    parts = re.split(r"(\d+)", normalized)
    natural = [int(part) if part.isdigit() else part for part in parts]
    return (0 if episode else 1, episode, natural)


def get_video_duration(video_path):
    try:
        from core.video_processor import _get_duration

        return _get_duration(video_path)
    except Exception:
        return 0.0


def format_duration(seconds):
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}分{s}秒"
