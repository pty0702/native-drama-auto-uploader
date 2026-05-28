from __future__ import annotations

from pathlib import Path
from typing import Callable

from core.image_processor import process_images
from core.template_generator import generate_template_image
from core.text_processor import generate_summary_and_name, save_results
from core.video_processor import process_videos
from utils.file_utils import IMAGE_EXTS, VIDEO_EXTS, find_files, find_txt

from .settings import AppConfig


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, str], None]


def run_generation_pipeline(
    source: str | Path,
    config: AppConfig,
    log_cb: LogFn | None = None,
    progress_cb: ProgressFn | None = None,
) -> Path:
    def log(message: str) -> None:
        if log_cb:
            log_cb(message)

    def progress(value: int, message: str) -> None:
        if progress_cb:
            progress_cb(value, message)

    source_path = Path(source)
    if not source_path.exists() or not source_path.is_dir():
        raise FileNotFoundError(f"源文件夹不存在: {source_path}")
    if not config.volc_api_key:
        raise ValueError("请先填写 API Key")

    output_base = Path(config.output_dir or source_path.parent)
    output_base.mkdir(parents=True, exist_ok=True)

    log(f"扫描源文件夹: {source_path}")
    video_files = find_files(str(source_path), VIDEO_EXTS)
    image_files = find_files(str(source_path), IMAGE_EXTS)
    txt_file = find_txt(str(source_path))

    if not txt_file:
        raise FileNotFoundError("源文件夹中未找到 TXT 简介文件")
    if not video_files:
        raise FileNotFoundError("源文件夹中未找到视频文件")

    log(f"发现: {len(video_files)} 个视频，{len(image_files)} 张图片，1 个 TXT")
    progress(5, "开始处理")

    log("步骤 1/4: 生成简介和短剧名")
    summary, sub_name = generate_summary_and_name(
        txt_file,
        config.volc_api_key,
        config.text_model,
        log_cb=log,
    )
    progress(25, f"短剧名: {sub_name}")

    temp_dir = output_base / "temp_processing"
    output_dir, _, _ = save_results(str(temp_dir), summary, sub_name)
    output_path = Path(output_dir)
    log(f"输出文件夹: {output_path}")
    progress(30, "文本处理完成")

    log("步骤 2/4: 处理视频")
    video_results, total_duration = process_videos(video_files, str(output_path), sub_name, log_cb=log)
    episode_count = len(video_results)
    progress(55, f"视频处理完成: {episode_count} 集")

    if image_files:
        log("步骤 3/4: 处理海报")
        process_images(
            image_files,
            str(output_path),
            sub_name,
            config.volc_api_key,
            image_model=config.image_model,
            target_w=config.image_target_width,
            target_h=config.image_target_height,
            log_cb=log,
        )
        progress(75, "海报处理完成")
    else:
        log("未发现图片文件，跳过海报处理")
        progress(75, "跳过海报处理")

    template_path = Path(config.template_image) if config.template_image else None
    if template_path and template_path.exists():
        log("步骤 4/4: 生成成本配置模板")
        generated_template = generate_template_image(
            str(template_path),
            str(output_path),
            sub_name,
            episode_count,
            total_duration,
            api_key=config.volc_api_key,
            api_base_url=config.api_base_url,
            image_model=config.image_model,
            log_cb=log,
        )
        generated = Path(generated_template)
        target = output_path / "模版.jpg"
        try:
            generated.replace(target)
        except Exception:
            target = generated
        log(f"模板文件: {target}")
        progress(90, "成本配置模板完成")
    else:
        log("未设置模板图片，跳过成本配置模板")
        progress(90, "跳过成本配置模板")

    progress(100, "生成完成")
    return output_path
