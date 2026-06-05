import os
import sys
import shutil
import subprocess
import re
from pathlib import Path

from utils.md5_modifier import modify_md5
from utils.file_utils import extract_episode_number


def _subprocess_no_window() -> dict:
    """返回 subprocess 隐藏控制台窗口的 creationflags（仅 Windows）。"""
    if os.name == "nt":
        return {"creationflags": subprocess.CREATE_NO_WINDOW}
    return {}


def _app_base_dirs():
    """返回开发环境和 PyInstaller 打包环境下可能存放外部工具的目录。"""
    dirs = []

    if getattr(sys, "frozen", False):
        dirs.append(Path(sys.executable).resolve().parent)

    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        dirs.append(Path(meipass).resolve())

    dirs.append(Path.cwd())
    dirs.append(Path(__file__).resolve().parents[1])

    unique_dirs = []
    seen = set()
    for base_dir in dirs:
        key = str(base_dir).lower()
        if key not in seen:
            seen.add(key)
            unique_dirs.append(base_dir)
    return unique_dirs


def _find_local_tool(tool_name):
    """查找软件目录旁的 ffmpeg/ffprobe 可执行文件。"""
    exe_name = f"{tool_name}.exe" if os.name == "nt" else tool_name

    for base_dir in _app_base_dirs():
        candidates = [
            base_dir / exe_name,
            base_dir / "bin" / exe_name,
            base_dir / "ffmpeg" / exe_name,
            base_dir / "ffmpeg" / "bin" / exe_name,
            base_dir / "_internal" / exe_name,
            base_dir / "_internal" / "bin" / exe_name,
            base_dir / "_internal" / "ffmpeg" / exe_name,
            base_dir / "_internal" / "ffmpeg" / "bin" / exe_name,
        ]
        for candidate in candidates:
            if candidate.is_file():
                return str(candidate)

        ffmpeg_dir = base_dir / "ffmpeg"
        if ffmpeg_dir.is_dir():
            for candidate in ffmpeg_dir.rglob(exe_name):
                if candidate.is_file():
                    return str(candidate)

    return None


def _find_ffmpeg():
    """查找交付包内置的 ffmpeg 可执行文件。"""
    local_ffmpeg = _find_local_tool("ffmpeg")
    if local_ffmpeg:
        return local_ffmpeg
    raise FileNotFoundError(
        "找不到内置 ffmpeg。请确认软件目录下存在 ffmpeg\\bin\\ffmpeg.exe 和 ffprobe.exe。"
    )


def _find_ffprobe():
    """查找交付包内置的 ffprobe 可执行文件。"""
    local_ffprobe = _find_local_tool("ffprobe")
    if local_ffprobe:
        return local_ffprobe

    # 尝试和 ffmpeg 同目录
    try:
        ffmpeg_dir = os.path.dirname(_find_ffmpeg())
        candidate = os.path.join(ffmpeg_dir, "ffprobe.exe" if os.name == "nt" else "ffprobe")
        if os.path.isfile(candidate):
            return candidate
    except FileNotFoundError:
        pass

    raise FileNotFoundError(
        "找不到内置 ffprobe。请确认软件目录下存在 ffmpeg\\bin\\ffprobe.exe。"
    )


_FFMPEG = None
_FFPROBE = None
_GPU_ENCODER = None  # cached result: "h264_nvenc" / "h264_amf" / "" (none)


def _get_ffmpeg():
    global _FFMPEG
    if _FFMPEG is None:
        _FFMPEG = _find_ffmpeg()
    return _FFMPEG


def _get_ffprobe():
    global _FFPROBE
    if _FFPROBE is None:
        _FFPROBE = _find_ffprobe()
    return _FFPROBE


def _test_encoder_available(ffmpeg, encoder):
    """实际尝试用编码器编码一帧，验证硬件是否真正可用。

    ffmpeg -encoders 只能看到编译时支持，不能反映当前机器是否插了对应显卡。
    这里用 nullsrc 生成极小测试帧做一次真实编码。"""
    try:
        subprocess.run(
            [ffmpeg, "-y", "-hide_banner", "-loglevel", "error",
             "-f", "lavfi", "-i", "nullsrc=s=32x32:d=0.1",
             "-frames:v", "1", "-c:v", encoder,
             "-f", "null", os.devnull],
            capture_output=True, timeout=10,
            **_subprocess_no_window(),
        )
        return True
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError):
        return False
    except Exception:
        return False


def detect_gpu_encoder(ffmpeg_path=None):
    """检测当前机器实际可用的 GPU 硬件编码器（真实编码测试，非仅编译标志）。

    返回 "h264_nvenc" / "h264_amf" / None（均不可用则走 CPU）。
    结果会被全局缓存，避免重复检测。"""
    global _GPU_ENCODER
    if _GPU_ENCODER is not None:
        return _GPU_ENCODER if _GPU_ENCODER != "" else None

    if ffmpeg_path is None:
        try:
            ffmpeg_path = _get_ffmpeg()
        except Exception:
            _GPU_ENCODER = ""
            return None

    # 快速预筛：检查 ffmpeg 编译时是否包含该编码器
    try:
        r = subprocess.run(
            [ffmpeg_path, "-hide_banner", "-encoders"],
            capture_output=True, text=True, timeout=15,
            **_subprocess_no_window(),
        )
        compiled = r.stdout
    except Exception:
        _GPU_ENCODER = ""
        return None

    # NVIDIA NVENC — 实际调用编码器测试硬件是否可用（优先：质量更好）
    if "h264_nvenc" in compiled:
        if _test_encoder_available(ffmpeg_path, "h264_nvenc"):
            _GPU_ENCODER = "h264_nvenc"
            return "h264_nvenc"

    # AMD AMF — 实际调用编码器测试硬件是否可用
    if "h264_amf" in compiled:
        if _test_encoder_available(ffmpeg_path, "h264_amf"):
            _GPU_ENCODER = "h264_amf"
            return "h264_amf"

    _GPU_ENCODER = ""
    return None



def get_gpu_encoder_label():
    """返回当前 GPU 编码器的中文显示名称，未检测到则返回None。"""
    try:
        ffmpeg = _get_ffmpeg()
        encoder = detect_gpu_encoder(ffmpeg)
    except Exception:
        return None
    if encoder == "h264_nvenc":
        return "NVIDIA NVENC"
    elif encoder == "h264_amf":
        return "AMD AMF"
    return None


def process_videos(video_files, output_dir, sub_name, log_cb=None):
    """转码视频，保留原始分辨率，修改 MD5，重命名并保存到输出目录。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    os.makedirs(output_dir, exist_ok=True)
    results = []
    total_duration = 0.0

    ffmpeg = _get_ffmpeg()
    log(f"ffmpeg: {ffmpeg}")

    gpu_encoder = detect_gpu_encoder(ffmpeg)
    if gpu_encoder == "h264_nvenc":
        log("检测到 NVENC 硬件加速可用")
    elif gpu_encoder == "h264_amf":
        log("检测到 AMF 硬件加速可用")
    else:
        log("未检测到 GPU 硬件编码器，将使用 CPU 编码")

    video_jobs = _build_video_jobs(video_files, output_dir, sub_name)
    for i, (vf, episode_num, output_path) in enumerate(video_jobs, 1):
        basename = os.path.basename(vf)
        log(f"正在处理视频 {i}/{len(video_files)}: {basename}")

        output_name = os.path.basename(output_path)

        duration = _get_duration(vf)
        total_duration += duration

        _transcode(vf, output_path, ffmpeg, gpu_encoder)

        modify_md5(output_path)

        size_mb = os.path.getsize(output_path) / (1024 * 1024)
        log(f"  完成: {output_name} ({size_mb:.1f}MB, {int(duration//60)}分{int(duration%60)}秒)")
        results.append(output_path)

    if len(results) != len(video_files):
        raise RuntimeError(f"视频处理数量异常: 输入 {len(video_files)} 个，输出 {len(results)} 个")

    log(f"视频处理全部完成: {len(results)}集, 总时长 {int(total_duration//60)}分{int(total_duration%60)}秒")
    return results, total_duration


def _build_video_jobs(video_files, output_dir, sub_name):
    jobs = []
    seen_episode = {}
    seen_output = {}

    for vf in video_files:
        basename = os.path.basename(vf)
        episode_num = _extract_episode(basename)
        if episode_num <= 0:
            raise ValueError(f"无法从视频文件名识别集数，请改成“第1集.mp4”格式: {basename}")

        if episode_num in seen_episode:
            previous = os.path.basename(seen_episode[episode_num])
            raise ValueError(f"发现重复集数 第{episode_num}集: {previous} / {basename}")
        seen_episode[episode_num] = vf

        output_name = f"{sub_name}-第{episode_num}集.mp4"
        output_path = os.path.join(output_dir, output_name)
        output_key = os.path.normcase(os.path.abspath(output_path))
        if output_key in seen_output:
            previous = os.path.basename(seen_output[output_key])
            raise ValueError(f"输出文件名重复，会导致覆盖: {previous} / {basename} -> {output_name}")
        seen_output[output_key] = vf
        jobs.append((vf, episode_num, output_path))

    jobs.sort(key=lambda item: item[1])
    return jobs


def _transcode(input_path, output_path, ffmpeg, gpu_encoder):
    """转码为 H.264 4Mbps，保留原始分辨率。优先 GPU 编码，失败自动降级到 CPU。"""
    if gpu_encoder:
        try:
            if gpu_encoder == "h264_nvenc":
                _transcode_nvenc(input_path, output_path, ffmpeg)
            elif gpu_encoder == "h264_amf":
                _transcode_amf(input_path, output_path, ffmpeg)
            return
        except subprocess.CalledProcessError:
            print(f"GPU 编码失败 ({gpu_encoder})，自动降级到 CPU 编码")
            if os.path.exists(output_path):
                os.remove(output_path)

    # 最终兜底：CPU 软件编码
    _transcode_cpu(input_path, output_path, ffmpeg)


def _transcode_nvenc(input_path, output_path, ffmpeg):
    """NVIDIA NVENC: 全 GPU 管线（解码 + 编码均在 GPU），保留原始分辨率。"""
    print("使用 NVENC GPU 硬件加速")
    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "cuda", "-hwaccel_output_format", "cuda",
        "-i", input_path,
        "-c:v", "h264_nvenc", "-preset", "p1", "-tune", "hq",
        "-b:v", "6000k", "-maxrate", "6000k", "-bufsize", "12000k",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=True,
                   **_subprocess_no_window())


def _transcode_amf(input_path, output_path, ffmpeg):
    """AMD AMF: D3D11VA 硬件解码 + GPU 编码，保留原始分辨率。"""
    print("使用 AMD AMF GPU 硬件加速")
    cmd = [
        ffmpeg, "-y",
        "-hwaccel", "d3d11va",
        "-i", input_path,
        "-c:v", "h264_amf", "-quality", "speed",
        "-b:v", "6000k", "-maxrate", "6000k", "-bufsize", "12000k",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=True,
                   **_subprocess_no_window())


def _transcode_cpu(input_path, output_path, ffmpeg):
    """CPU 软件编码 (libx264)，保留原始分辨率，作为 GPU 路径的最终兜底。"""
    print("使用 CPU 软件编码 (libx264)")
    cmd = [
        ffmpeg, "-y", "-i", input_path,
        "-c:v", "libx264", "-preset", "ultrafast",
        "-b:v", "6000k", "-maxrate", "6000k", "-bufsize", "12000k",
        "-c:a", "copy",
        "-movflags", "+faststart",
        output_path,
    ]
    subprocess.run(cmd, capture_output=True, timeout=600, check=True,
                   **_subprocess_no_window())


def _get_duration(path):
    try:
        ffprobe = _get_ffprobe()
    except FileNotFoundError:
        return 0.0
    if not ffprobe:
        return 0.0
    try:
        r = subprocess.run(
            [ffprobe, "-v", "error", "-show_entries", "format=duration",
             "-of", "default=noprint_wrappers=1:nokey=1", path],
            capture_output=True, text=True, timeout=10,
            **_subprocess_no_window(),
        )
        return float(r.stdout.strip())
    except Exception:
        return 0.0


def _extract_episode(filename):
    return extract_episode_number(filename)
