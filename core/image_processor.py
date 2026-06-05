"""
海报图片处理器

使用 gpt-image-2 (images/generations API) 生成新的短剧海报。
"""
from __future__ import annotations

import base64
import os
from io import BytesIO

import requests
from PIL import Image, ImageOps

from core.ark_image import generate_ark_image, is_ark_image_api
from core.dashscope_image import generate_dashscope_image, is_dashscope_api
from native_drama_uploader.settings import IMAGE_API_BASE_URL, IMAGE_MODEL

# 绕过系统代理
_session = requests.Session()
_session.trust_env = False


def _normalize_api_base_url(api_base_url):
    api_base_url = (api_base_url or IMAGE_API_BASE_URL).rstrip("/")
    if not api_base_url:
        raise RuntimeError("未配置图片 API 地址")
    if is_ark_image_api(api_base_url):
        if api_base_url.endswith("/api/v3"):
            return api_base_url
        if api_base_url.endswith("/api"):
            return f"{api_base_url}/v3"
        return f"{api_base_url}/api/v3"
    if is_dashscope_api(api_base_url):
        if api_base_url.endswith("/api/v1"):
            return api_base_url
        if api_base_url.endswith("/api"):
            return f"{api_base_url}/v1"
        return f"{api_base_url}/api/v1"
    if not api_base_url.endswith("/v1"):
        api_base_url = f"{api_base_url}/v1"
    return api_base_url


def process_images(
    image_files, output_dir, sub_name, api_key=None, api_base_url=None,
    image_model=None, target_w=816, target_h=1086, synopsis=None, log_cb=None,
):
    """对每张海报图片：用 gpt-image-2 生成新海报，仅使用在线 API。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    os.makedirs(output_dir, exist_ok=True)
    results = []

    for i, img_path in enumerate(image_files, 1):
        log(f"正在处理海报图 {i}/{len(image_files)}: {os.path.basename(img_path)}")

        new_img = _generate_poster(
            img_path,
            sub_name,
            api_key,
            api_base_url,
            image_model,
            synopsis,
            target_w,
            target_h,
            log,
        )

        ext = os.path.splitext(img_path)[1].lower()
        if ext not in (".jpg", ".jpeg", ".png"):
            ext = ".jpg"
        out_name = f"{sub_name}{ext}"
        out_path = os.path.join(output_dir, out_name)
        if os.path.exists(out_path):
            base, e = os.path.splitext(out_name)
            out_name = f"{base}_{i}{e}"
            out_path = os.path.join(output_dir, out_name)

        if ext in (".jpg", ".jpeg"):
            new_img.save(out_path, quality=95)
        else:
            new_img.save(out_path)
        log(f"  完成: {out_name}")
        results.append(out_path)

    return results


def _fit_poster(img: Image.Image, target_w: int, target_h: int) -> Image.Image:
    """等比裁切到目标尺寸，避免直接拉伸导致人物变形。"""
    return ImageOps.fit(img.convert("RGB"), (target_w, target_h), method=Image.LANCZOS, centering=(0.5, 0.45))


def _build_poster_prompt(sub_name: str, synopsis: str | None) -> str:
    synopsis = (synopsis or "").strip()
    if len(synopsis) > 120:
        synopsis = synopsis[:120]
    synopsis_hint = f"剧情摘要：{synopsis}。" if synopsis else ""
    return (
        f"请严格参考输入参考图，保留原海报的核心人物关系、题材气质、构图重心、色彩氛围与风格类型，"
        f"重新生成一张更成熟、更像可上架微短剧成品的竖版海报。"
        f"{synopsis_hint}"
        f"剧名必须是「{sub_name}」。"
        "要求："
        "1. 保留参考图的主体设定与情绪，不要偏题，不要生成无关场景；"
        "2. 标题使用清晰、准确、易读的中文，避免错字漏字；"
        "3. 标题控制在顶部安全区域，不遮挡人物脸部和关键动作；"
        "4. 整体要像精品短剧封面，而不是普通插画、海报样机或拼贴图；"
        "5. 画面层次清楚，人物突出，适合手机端封面浏览；"
        "6. 不要添加水印、二维码、英文、小字说明、边框；"
        "7. 输出竖版 3:4 海报观感。"
    )


def _generate_poster(img_path, sub_name, api_key, api_base_url, image_model, synopsis, target_w, target_h, log):
    """用 gpt-image-2 生成带有新剧名的海报。"""
    api_key = api_key or ""
    if not api_key:
        raise RuntimeError("未配置图片 API Key")
    api_base_url = _normalize_api_base_url(api_base_url)
    image_model = image_model or IMAGE_MODEL

    prompt = _build_poster_prompt(sub_name, synopsis)

    if is_ark_image_api(api_base_url):
        log(f"  正在调用火山方舟 {image_model} 生图...")
        img = generate_ark_image(
            session=_session,
            api_base_url=api_base_url,
            api_key=api_key,
            model=image_model,
            prompt=prompt,
            image_paths=[img_path],
            size="2K",
            response_format="url",
            output_format="png",
            watermark=True,
            sequential_image_generation="disabled",
            stream=False,
            timeout=300,
        )
        return _fit_poster(img, target_w, target_h)

    if is_dashscope_api(api_base_url):
        log(f"  正在调用阿里云百炼 {image_model} 生图...")
        img = generate_dashscope_image(
            session=_session,
            api_base_url=api_base_url,
            api_key=api_key,
            model=image_model,
            prompt=prompt,
            image_paths=[img_path],
            size="1080*1440",
            n=1,
            timeout=300,
            negative_prompt="低清晰度，模糊文字，错误标题，畸形人物，过强AI感，多余文字，水印，边框",
            prompt_extend=True,
            watermark=False,
        )
        return _fit_poster(img, target_w, target_h)

    # 如需切回 OpenRouter，可恢复下面这段分支逻辑。
    # if is_openrouter_api(api_base_url):
    #     log(f"  正在调用 OpenRouter {image_model} 生图...")
    #     img = generate_openrouter_image(
    #         session=_session,
    #         api_base_url=api_base_url,
    #         api_key=api_key,
    #         model=image_model,
    #         prompt=prompt,
    #         aspect_ratio="3:4",
    #         image_size="1K",
    #         timeout=300,
    #     )
    #     return img.resize((816, 1086), Image.LANCZOS)

    url = f"{api_base_url}/images/generations"
    payload = {
        "model": image_model,
        "prompt": prompt,
        "size": "1024x1024",
        "quality": "high",
        "n": 1,
        "response_format": "b64_json",
    }

    log(f"  正在调用 {image_model} 生图...")
    response = _session.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=300,
    )

    if response.status_code != 200:
        raise RuntimeError(f"API 返回 HTTP {response.status_code}: {response.text[:200]}")

    data = response.json()
    images = data.get("data", [])
    if not images or "b64_json" not in images[0]:
        raise RuntimeError(f"API 返回数据异常: {list(images[0].keys()) if images else 'empty'}")

    img_bytes = base64.b64decode(images[0]["b64_json"])
    img = Image.open(BytesIO(img_bytes))
    return _fit_poster(img, target_w, target_h)
