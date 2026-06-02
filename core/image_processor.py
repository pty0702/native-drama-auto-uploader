"""
海报图片处理器

使用 gpt-image-2 (images/generations API) 生成新的短剧海报。
"""
from __future__ import annotations

import base64
import os
from io import BytesIO

import requests
from PIL import Image, ImageDraw, ImageFont

from native_drama_uploader.settings import IMAGE_API_BASE_URL, IMAGE_MODEL

# 绕过系统代理
_session = requests.Session()
_session.trust_env = False


def _normalize_api_base_url(api_base_url):
    api_base_url = (api_base_url or IMAGE_API_BASE_URL).rstrip("/")
    if not api_base_url:
        raise RuntimeError("未配置图片 API 地址")
    if not api_base_url.endswith("/v1"):
        api_base_url = f"{api_base_url}/v1"
    return api_base_url


def process_images(
    image_files, output_dir, sub_name, api_key=None, api_base_url=None,
    image_model=None, target_w=816, target_h=1086, log_cb=None,
):
    """对每张海报图片：用 gpt-image-2 生成新海报，失败时本地方案兜底。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    os.makedirs(output_dir, exist_ok=True)
    results = []

    for i, img_path in enumerate(image_files, 1):
        log(f"正在处理海报图 {i}/{len(image_files)}: {os.path.basename(img_path)}")

        try:
            new_img = _generate_poster(img_path, sub_name, api_key, api_base_url, image_model, log)
        except Exception as e:
            log(f"  AI 生图失败({e})，使用本地方案...")
            new_img = _replace_text_local(img_path, sub_name, target_w, target_h)

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


def _generate_poster(img_path, sub_name, api_key, api_base_url, image_model, log):
    """用 gpt-image-2 生成带有新剧名的海报。"""
    api_key = api_key or ""
    if not api_key:
        raise RuntimeError("未配置图片 API Key")
    api_base_url = _normalize_api_base_url(api_base_url)
    image_model = image_model or IMAGE_MODEL

    prompt = (
        f"一张微短剧竖版海报，顶部大字标题「{sub_name}」，"
        f"现代都市爱情风格，高质量设计，精美排版，"
        f"深色背景配金色或白色艺术字体，电影海报质感，816x1086比例"
    )

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
    return img.resize((816, 1086), Image.LANCZOS)


def _replace_text_local(img_path, sub_name, target_w=816, target_h=1086):
    """本地备选方案：在图片上覆盖绘制新剧名。"""
    img = Image.open(img_path)
    draw = ImageDraw.Draw(img)

    font_size = max(40, img.width // 15)
    try:
        font = ImageFont.truetype("msyh.ttc", font_size)
    except Exception:
        font = ImageFont.load_default()

    bbox = draw.textbbox((0, 0), sub_name, font=font)
    tw, th = bbox[2] - bbox[0], bbox[3] - bbox[1]
    x = (img.width - tw) // 2
    y = img.height - th - 80

    draw.rectangle([x - 20, y - 10, x + tw + 20, y + th + 10], fill="black")
    draw.text((x, y), sub_name, fill="white", font=font)

    return img.resize((target_w, target_h), Image.LANCZOS)
