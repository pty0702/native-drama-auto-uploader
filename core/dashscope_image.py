from __future__ import annotations

import base64
import mimetypes
from io import BytesIO
from pathlib import Path

import requests
from PIL import Image


def is_dashscope_api(api_base_url: str | None) -> bool:
    base = (api_base_url or "").lower()
    return "dashscope.aliyuncs.com" in base or ".maas.aliyuncs.com" in base


def normalize_dashscope_base_url(api_base_url: str | None) -> str:
    base = (api_base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("未配置阿里云百炼 API 地址")
    if base.endswith("/api/v1"):
        return base
    if base.endswith("/api"):
        return f"{base}/v1"
    return f"{base}/api/v1"


def generate_dashscope_image(
    session: requests.Session,
    api_base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_paths: list[str | Path] | None = None,
    size: str | None = None,
    n: int = 1,
    timeout: int = 300,
    negative_prompt: str | None = None,
    prompt_extend: bool | None = None,
    watermark: bool = False,
) -> Image.Image:
    url = f"{normalize_dashscope_base_url(api_base_url)}/services/aigc/multimodal-generation/generation"
    content: list[dict[str, str]] = []
    for image_path in image_paths or []:
        content.append({"image": _path_to_data_url(image_path)})
    content.append({"text": prompt})

    parameters: dict[str, object] = {
        "n": n,
        "watermark": watermark,
    }
    if size:
        parameters["size"] = size
    if negative_prompt is not None:
        parameters["negative_prompt"] = negative_prompt
    if prompt_extend is not None:
        parameters["prompt_extend"] = prompt_extend

    response = session.post(
        url,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "input": {
                "messages": [
                    {
                        "role": "user",
                        "content": content,
                    }
                ]
            },
            "parameters": parameters,
        },
        timeout=timeout,
    )
    if response.status_code != 200:
        raise RuntimeError(f"阿里云百炼生图失败 HTTP {response.status_code}: {response.text[:500]}")
    return decode_dashscope_image_response(session, response)


def decode_dashscope_image_response(session: requests.Session, response: requests.Response) -> Image.Image:
    try:
        result = response.json()
    except Exception as exc:
        raise RuntimeError(f"阿里云百炼返回不是 JSON: {exc}") from exc

    output = result.get("output") or {}
    choices = output.get("choices") or []
    if not choices:
        raise RuntimeError(f"阿里云百炼返回缺少 choices: {str(result)[:500]}")

    message = choices[0].get("message") or {}
    contents = message.get("content") or []
    image_url = ""
    for item in contents:
        if item.get("image"):
            image_url = item["image"]
            break
    if not image_url:
        raise RuntimeError(f"阿里云百炼返回缺少图片字段: {str(message)[:500]}")

    downloaded = session.get(image_url, timeout=120)
    if downloaded.status_code != 200:
        raise RuntimeError(f"下载阿里云百炼图片失败 HTTP {downloaded.status_code}: {downloaded.text[:200]}")
    return Image.open(BytesIO(downloaded.content)).convert("RGB")


def _path_to_data_url(path: str | Path) -> str:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(str(file_path))[0] or "image/png"
    data = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"
