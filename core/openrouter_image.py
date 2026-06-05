from __future__ import annotations

import base64
import mimetypes
from io import BytesIO

import requests
from PIL import Image


def is_openrouter_api(api_base_url: str | None) -> bool:
    return "openrouter.ai" in (api_base_url or "").lower()


def image_to_data_url(path: str) -> str:
    mime_type = mimetypes.guess_type(path)[0] or "image/jpeg"
    with open(path, "rb") as file:
        encoded = base64.b64encode(file.read()).decode("ascii")
    return f"data:{mime_type};base64,{encoded}"


def generate_openrouter_image(
    session: requests.Session,
    api_base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_paths: list[str] | None = None,
    aspect_ratio: str | None = None,
    image_size: str | None = None,
    timeout: int = 300,
) -> Image.Image:
    api_base_url = api_base_url.rstrip("/")
    if not api_base_url.endswith("/v1"):
        api_base_url = f"{api_base_url}/v1"

    content: list[dict] = [{"type": "text", "text": prompt}]
    for path in image_paths or []:
        content.append(
            {
                "type": "image_url",
                "image_url": {"url": image_to_data_url(path)},
            }
        )

    payload = {
        "model": model,
        "messages": [{"role": "user", "content": content}],
        "modalities": ["image", "text"],
    }
    image_config = {}
    if aspect_ratio:
        image_config["aspect_ratio"] = aspect_ratio
    if image_size:
        image_config["image_size"] = image_size
    if image_config:
        payload["image_config"] = image_config

    response = session.post(
        f"{api_base_url}/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json=payload,
        timeout=timeout,
    )
    if response.status_code != 200:
        if response.status_code == 403 and "not available in your region" in response.text.lower():
            raise RuntimeError(
                "OpenRouter 生图失败 HTTP 403: 当前请求出口 IP/地区不可用。"
                "如果客户后台显示模型可用，请在客户电脑或客户网络环境下测试。"
                f"原始返回: {response.text[:300]}"
            )
        raise RuntimeError(f"OpenRouter 生图失败 HTTP {response.status_code}: {response.text[:500]}")

    return decode_openrouter_image_response(session, response)


def decode_openrouter_image_response(session: requests.Session, response: requests.Response) -> Image.Image:
    try:
        result = response.json()
    except Exception as exc:
        raise RuntimeError(f"OpenRouter 返回不是 JSON: {exc}") from exc

    choices = result.get("choices") or []
    if not choices:
        raise RuntimeError(f"OpenRouter 返回缺少 choices: {str(result)[:500]}")

    message = choices[0].get("message") or {}
    images = message.get("images") or []
    image_url = _first_image_url(images)

    if not image_url:
        image_url = _first_image_url_from_content(message.get("content"))

    if not image_url:
        raise RuntimeError(f"OpenRouter 返回缺少图片字段: {str(message)[:500]}")

    if image_url.startswith("data:image/"):
        _, encoded = image_url.split(",", 1)
        return Image.open(BytesIO(base64.b64decode(encoded))).convert("RGB")

    downloaded = session.get(image_url, timeout=120)
    if downloaded.status_code != 200:
        raise RuntimeError(f"下载 OpenRouter 图片失败 HTTP {downloaded.status_code}: {downloaded.text[:200]}")
    return Image.open(BytesIO(downloaded.content)).convert("RGB")


def _first_image_url(images) -> str | None:
    for item in images or []:
        image_url = item.get("image_url") if isinstance(item, dict) else None
        if isinstance(image_url, str):
            return image_url
        if isinstance(image_url, dict) and image_url.get("url"):
            return image_url["url"]
    return None


def _first_image_url_from_content(content) -> str | None:
    if isinstance(content, str):
        return None
    for item in content or []:
        if not isinstance(item, dict):
            continue
        image_url = item.get("image_url")
        if isinstance(image_url, str):
            return image_url
        if isinstance(image_url, dict) and image_url.get("url"):
            return image_url["url"]
    return None
