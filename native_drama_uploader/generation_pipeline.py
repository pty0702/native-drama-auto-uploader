from __future__ import annotations

import logging
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Callable

from PIL import Image

from core.image_processor import process_images
from core.template_generator import generate_template_image
from core.text_processor import generate_summary_and_name
from core.video_processor import process_videos
from utils.file_utils import IMAGE_EXTS, VIDEO_EXTS, find_files, find_txt

from .queue_store import QueueStore
from .settings import AppConfig, DEBUG_DIR, IMAGE_MODEL, SUCAI_DIR, TEXT_MODEL


LogFn = Callable[[str], None]
ProgressFn = Callable[[int, str], None]
REQUIRED_SUCAI_FILES = ("视频.docx", "模板.jpg", "照片参考.png")


class DuplicateDramaNameSkipped(RuntimeError):
    """同一天内已经存在同名剧目时跳过本次生成。"""


def _setup_file_logger() -> logging.Logger:
    """创建写入 debug/ 目录的文件日志。"""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    log_path = DEBUG_DIR / f"generation_{timestamp}.log"

    logger = logging.getLogger(f"generation_{timestamp}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    fh = logging.FileHandler(str(log_path), encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(fh)

    logger.info("=" * 60)
    logger.info("短剧生成流程启动")
    logger.info("=" * 60)
    return logger


def find_named_image(folder: str | Path, stem: str) -> Path | None:
    folder_path = Path(folder)
    for ext in IMAGE_EXTS:
        candidate = folder_path / f"{stem}{ext}"
        if candidate.exists() and candidate.is_file():
            return candidate
    return None


def find_folder_named_cover(folder: str | Path) -> Path | None:
    """源视频包海报必须与文件夹同名，例如 婚后失忆.jpg。"""
    folder_path = Path(folder)
    return find_named_image(folder_path, folder_path.name)


def find_source_bmp_proofs(folder: str | Path) -> list[Path]:
    """源素材包里的 BMP 图片全部作为证明材料。"""
    folder_path = Path(folder)
    return sorted(
        [p for p in folder_path.iterdir() if p.is_file() and p.suffix.lower() == ".bmp"],
        key=lambda p: p.name.lower(),
    )


def convert_bmp_proofs(proof_src: list[Path], output_dir: Path, log: LogFn) -> list[Path]:
    """将源 BMP 证明材料转为成品目录里的 证明1.jpg、证明2.jpg。"""
    results: list[Path] = []
    for index, proof_img in enumerate(proof_src, 1):
        dst = output_dir / f"证明{index}.jpg"
        with Image.open(str(proof_img)) as img:
            if img.mode != "RGB":
                img = img.convert("RGB")
            img.save(str(dst), format="JPEG", quality=95, optimize=True)
        results.append(dst)
        log(f"  已生成证明材料: {dst.name} <- {proof_img.name}")
    return results


def validate_generation_inputs(sources: list[str | Path]) -> list[str]:
    """流水线启动前校验公共素材和每个视频包的必需文件。"""
    errors: list[str] = []

    for filename in REQUIRED_SUCAI_FILES:
        path = SUCAI_DIR / filename
        if not path.exists() or not path.is_file():
            errors.append(f"缺少公共素材: {path}")

    for source in sources:
        source_path = Path(source)
        if not source_path.exists() or not source_path.is_dir():
            errors.append(f"视频包不存在: {source_path}")
            continue
        if not find_files(str(source_path), VIDEO_EXTS):
            errors.append(f"视频包缺少视频文件: {source_path}")
        if not find_txt(str(source_path)):
            errors.append(f"视频包缺少简介 txt: {source_path}")
        if not find_folder_named_cover(source_path):
            errors.append(f"视频包缺少海报图片，必须命名为 文件夹名.jpg/png/jpeg: {source_path}")

    return errors


def _today_duplicate_reason(drama_name: str, output_path: Path) -> str | None:
    today = datetime.now().date().isoformat()
    for task in QueueStore().load():
        task_day = (task.created_at or task.updated_at or "")[:10]
        if task.drama_name == drama_name and task_day == today and task.status in {"pending", "uploading", "success"}:
            return f"今日队列中已存在同名剧目({task.status}): {drama_name}"

    if output_path.exists():
        try:
            mtime_day = datetime.fromtimestamp(output_path.stat().st_mtime).date().isoformat()
        except OSError:
            mtime_day = ""
        if mtime_day == today:
            return f"今日已生成同名成品文件夹: {output_path}"
    return None


def _cleanup_processing_outputs(source: str | Path, config: AppConfig, log: LogFn) -> None:
    """生成失败时清理隐藏的临时成品目录，避免客户误用半成品。"""
    source_path = Path(source)
    output_base = Path(config.output_dir or source_path.parent)
    if not output_base.exists():
        return

    for candidate in output_base.iterdir():
        if not candidate.is_dir():
            continue
        if candidate.name.startswith(".") and ".processing_" in candidate.name:
            try:
                shutil.rmtree(str(candidate))
                log(f"已清理临时成品目录: {candidate}")
            except Exception as cleanup_exc:
                log(f"临时成品目录清理失败，请手动处理: {candidate} / {cleanup_exc}")


def run_generation_pipeline(
    source: str | Path,
    config: AppConfig,
    log_cb: LogFn | None = None,
    progress_cb: ProgressFn | None = None,
) -> Path:
    file_log = _setup_file_logger()

    def log(message: str) -> None:
        file_log.info(message)
        if log_cb:
            log_cb(message)

    def progress(value: int, message: str) -> None:
        file_log.info(f"[{value}%] {message}")
        if progress_cb:
            progress_cb(value, message)

    try:
        return _run_pipeline(source, config, log, progress)
    except Exception as exc:
        _cleanup_processing_outputs(source, config, log)
        file_log.error(f"生成失败: {exc}")
        file_log.debug(traceback.format_exc())
        raise


def _run_pipeline(
    source: str | Path,
    config: AppConfig,
    log: LogFn,
    progress: ProgressFn,
) -> Path:
    source_path = Path(source)
    if not source_path.exists() or not source_path.is_dir():
        raise FileNotFoundError(f"源文件夹不存在: {source_path}")
    if not config.volc_api_key:
        raise ValueError("请先填写 API Key")
    input_errors = validate_generation_inputs([source_path])
    if input_errors:
        raise ValueError("流水线启动前检查未通过:\n" + "\n".join(input_errors))

    log(f"源文件夹: {source_path}")
    log(f"输出目录: {config.output_dir}")
    text_model = config.text_model or TEXT_MODEL
    image_model = config.image_model or IMAGE_MODEL
    log(f"文本模型: {text_model}, 图像模型: {image_model}")

    output_base = Path(config.output_dir or source_path.parent)
    output_base.mkdir(parents=True, exist_ok=True)

    log(f"扫描源文件夹: {source_path}")
    video_files = find_files(str(source_path), VIDEO_EXTS)
    txt_file = find_txt(str(source_path))

    if not txt_file:
        raise FileNotFoundError("源文件夹中未找到 TXT 简介文件")
    if not video_files:
        raise FileNotFoundError("源文件夹中未找到视频文件")
    # 源包证明材料只从 BMP 图片提取，生成时统一转为 证明1.jpg、证明2.jpg...
    proof_src = find_source_bmp_proofs(source_path)
    poster_path = find_folder_named_cover(source_path)
    poster_src = [str(poster_path)] if poster_path else []

    if not poster_src:
        raise FileNotFoundError("源文件夹中未找到海报图片（必须命名为 文件夹名.jpg/png/jpeg）")

    log(f"发现: {len(video_files)} 个视频，{len(poster_src)} 张海报，{len(proof_src)} 张 BMP 证明，1 个 TXT")
    log(f"视频文件: {video_files}")
    log(f"海报图片: {poster_src}")
    log(f"BMP 证明材料: {[str(p) for p in proof_src]}")
    log(f"TXT 文件: {txt_file}")
    progress(5, "开始处理")

    log("步骤 1/4: 生成简介和短剧名")
    summary, sub_name = generate_summary_and_name(
        txt_file,
        config.volc_api_key,
        text_model,
        api_base_url=config.api_base_url,
        log_cb=log,
    )
    if len(summary) > 100:
        summary = summary[:100]
        log("简介超过 100 字，已自动截断到 100 字")
    progress(25, f"短剧名: {sub_name}")

    # 输出文件夹、上传剧名、视频名前缀都使用新生成的短剧名。
    folder_name = sub_name
    final_output_path = output_base / folder_name
    work_path = output_base / f".{folder_name}.processing_{datetime.now().strftime('%Y%m%d_%H%M%S')}"
    duplicate_reason = _today_duplicate_reason(folder_name, final_output_path)
    if duplicate_reason:
        message = f"跳过生成: {duplicate_reason}。为避免同一天重复上传同名剧目，本次不转码、不入队。"
        log(message)
        progress(100, message)
        raise DuplicateDramaNameSkipped(message)
    if work_path.exists():
        shutil.rmtree(str(work_path))
    work_path.mkdir(parents=True, exist_ok=True)

    # 保存简介和副名
    (work_path / "简介.txt").write_text(summary, encoding="utf-8")
    (work_path / "副名.txt").write_text(sub_name, encoding="utf-8")

    log(f"临时输出文件夹: {work_path}")
    progress(30, "文本处理完成")

    log("步骤 2/4: 处理视频")
    # 视频命名必须用完整文件夹名（= 表单里的剧目名称），否则微信校验失败
    video_results, total_duration = process_videos(video_files, str(work_path), folder_name, log_cb=log)
    episode_count = len(video_results)
    progress(55, f"视频处理完成: {episode_count} 集")

    # 源 BMP 证明材料转为成品文件夹里的 证明1.jpg、证明2.jpg...
    if proof_src:
        log("转换 BMP 证明材料...")
        convert_bmp_proofs(proof_src, work_path, log)

    log("步骤 3/4: 处理海报")
    poster_results = process_images(
        poster_src,
        str(work_path),
        sub_name,
        api_key=config.image_api_key,
        api_base_url=config.image_api_base_url,
        image_model=image_model,
        log_cb=log,
    )
    # 将第一张 AI 海报重命名为成品文件夹同名图片，确保上传时能自动识别为封面
    if poster_results:
        first_poster = Path(poster_results[0])
        cover_dst = first_poster.parent / f"{folder_name}{first_poster.suffix}"
        if first_poster != cover_dst:
            first_poster.rename(cover_dst)
            log(f"  封面已命名: {cover_dst.name}")
    progress(75, "海报处理完成")

    # 步骤 4: 成本配置模板 — 在转码后的成品文件夹里生成新的模版.jpg
    log("步骤 4/4: 生成并审查成本配置模板...")
    docx_template = SUCAI_DIR / "视频.docx"
    stamp_image = SUCAI_DIR / "模板.jpg"
    if docx_template.exists() and stamp_image.exists():
        generate_template_image(
            docx_template,
            str(work_path),
            sub_name,
            episode_count,
            total_duration,
            stamp_image=stamp_image,
            api_key=config.image_api_key,
            api_base_url=config.image_api_base_url,
            image_model=image_model,
            log_cb=log,
        )
        progress(90, "成本配置模板生成完成")
    else:
        log(f"  docx 或印章底图不存在，跳过: {docx_template} / {stamp_image}")
        progress(90, "跳过成本配置模板")

    if len(list(work_path.glob("*.mp4"))) != len(video_files):
        raise RuntimeError(
            f"成品视频数量校验失败: 输入 {len(video_files)} 个，输出 {len(list(work_path.glob('*.mp4')))} 个"
        )

    if final_output_path.exists():
        shutil.rmtree(str(final_output_path))
    work_path.rename(final_output_path)
    log(f"最终输出文件夹: {final_output_path}")

    try:
        if source_path.exists() and source_path.resolve() != final_output_path.resolve():
            shutil.rmtree(str(source_path))
            log(f"原始导入文件夹已删除: {source_path}")
    except Exception as exc:
        log(f"原始导入文件夹删除失败，请手动处理: {source_path} / {exc}")

    progress(100, "生成完成")
    return final_output_path
