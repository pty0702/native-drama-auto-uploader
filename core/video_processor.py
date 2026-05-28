import os
import subprocess
import re
from utils.md5_modifier import modify_md5


def process_videos(video_files, output_dir, sub_name, log_cb=None):
    """转码视频为 2K，修改 MD5，重命名并保存到输出目录。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    os.makedirs(output_dir, exist_ok=True)
    results = []
    total_duration = 0.0

    for i, vf in enumerate(video_files, 1):
        basename = os.path.basename(vf)
        log(f"正在处理视频 {i}/{len(video_files)}: {basename}")

        episode_num = _extract_episode(basename)
        output_name = f"{sub_name}-第{episode_num}集.mp4"
        output_path = os.path.join(output_dir, output_name)

        duration = _get_duration(vf)
        total_duration += duration

        _transcode(vf, output_path)

        modify_md5(output_path)

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(f"  完成: {output_name} ({size_mb:.1f}MB, {int(duration//60)}分{int(duration%60)}秒)")
        results.append(output_path)

    log(f"视频处理全部完成: {len(results)}集, 总时长 {int(total_duration//60)}分{int(total_duration%60)}秒")
    return results, total_duration


def _transcode(input_path, output_path):
    """用 ffmpeg 转码为 2K 分辨率。"""
    cmd = [
        "ffmpeg", "-y", "-i", input_path,
        "-vf", "scale=2560:1440:force_original_aspect_ratio=decrease,pad=2560:1440:(ow-iw)/2:(oh-ih)/2",
        "-c:v", "libx264", "-preset", "medium",
        "-b:v", "8000k", "-maxrate", "8000k", "-bufsize", "16000k",
        "-c:a", "aac", "-b:a", "128k",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=True)


def _get_duration(path):
    try:
        r = subprocess.run(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _extract_episode(filename):
    m = re.search(r"第(\d+)集", filename)
    if m:
        return int(m.group(1))
    m = re.search(r"(\d+)", filename)
    if m:
        return int(m.group(1))
    return 0
