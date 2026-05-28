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
    return sorted(result)


def find_txt(folder):
    for f in os.listdir(folder):
        if f.lower().endswith(".txt"):
            return os.path.join(folder, f)
    return None


def extract_episode_number(filename):
    name = os.path.splitext(os.path.basename(filename))[0]
    m = re.search(r"第(\d+)集", name)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", name)
    if m:
        return int(m.group(1))
    return 0


def get_video_duration(video_path):
    import subprocess
    try:
        result = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", video_path],
            capture_output=True, text=True, timeout=10
        )
        return float(result.stdout.strip())
    except Exception:
        return 0.0


def format_duration(seconds):
    m = int(seconds) // 60
    s = int(seconds) % 60
    return f"{m}分{s}秒"
