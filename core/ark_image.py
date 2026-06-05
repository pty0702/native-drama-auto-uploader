from __future__ import annotations

import base64
import mimetypes
from io import BytesIO
from pathlib import Path
from typing import Any

import requests
from PIL import Image


def is_ark_image_api(api_base_url: str | None) -> bool:
    return "ark.cn-beijing.volces.com" in (api_base_url or "").lower()


def normalize_ark_base_url(api_base_url: str | None) -> str:
    base = (api_base_url or "").rstrip("/")
    if not base:
        raise RuntimeError("未配置火山方舟图片 API 地址")
    if base.endswith("/api/v3"):
        return base
    if base.endswith("/api"):
        return f"{base}/v3"
    return f"{base}/api/v3"


def generate_ark_image(
    session: requests.Session,
    api_base_url: str,
    api_key: str,
    model: str,
    prompt: str,
    image_paths: list[str | Path] | None = None,
    size: str = "2K",
    response_format: str = "url",
    output_format: str = "png",
    watermark: bool = True,
    sequential_image_generation: str = "disabled",
    stream: bool = False,
    timeout: int = 300,
) -> Image.Image:
    url = f"{normalize_ark_base_url(api_base_url)}/images/generations"
    payload: dict[str, object] = {
        "model": model,
        "prompt": prompt,
        "size": size,
        "response_format": response_format,
        "output_format": output_format,
        "watermark": watermark,
        "sequential_image_generation": sequential_image_generation,
        "stream": stream,
    }
    if image_paths:
        payload["image"] = [_path_to_data_url(path) for path in image_paths]

    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    try:
        response = session.post(url, headers=headers, json=payload, timeout=timeout)
    except requests.RequestException as exc:
        diagnosis = _diagnose_ark_api(session, api_base_url, api_key)
        raise RuntimeError(f"火山方舟生图请求失败，诊断结果: {diagnosis}; 底层连接错误: {exc}") from exc
    if response.status_code != 200:
        raise RuntimeError(f"火山方舟生图失败 HTTP {response.status_code}: {_format_ark_error(response)}")
    return decode_ark_image_response(session, response)


def _diagnose_ark_api(session: requests.Session, api_base_url: str, api_key: str) -> str:
    """When large image payloads are cut off, send a tiny request to expose key/network errors."""
    url = f"{normalize_ark_base_url(api_base_url)}/images/generations"
    payload: dict[str, Any] = {
        "model": "__recreate_key_check__",
        "prompt": "key check",
        "size": "2K",
        "response_format": "url",
        "watermark": True,
        "sequential_image_generation": "disabled",
        "stream": False,
    }
    try:
        response = session.post(
            url,
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json=payload,
            timeout=30,
        )
    except requests.RequestException as exc:
        return f"接口连接失败，可能是本机网络、代理、防火墙或 TLS 连接问题: {exc}"
    if response.status_code in (401, 403):
        return f"API Key 不可用或未激活: {_format_ark_error(response)}"
    if response.status_code == 400:
        return "接口可连通，API Key 可被服务端识别；原请求可能被大图 payload、模型权限或服务端限流中断"
    if response.status_code == 429:
        return f"接口限流或额度不足: {_format_ark_error(response)}"
    if response.status_code >= 500:
        return f"火山方舟服务端异常: HTTP {response.status_code}: {response.text[:300]}"
    return f"接口返回 HTTP {response.status_code}: {response.text[:300]}"


def _format_ark_error(response: requests.Response) -> str:
    try:
        payload = response.json()
    except Exception:
        return response.text[:500]
    error = payload.get("error")
    if isinstance(error, dict):
        code = error.get("code") or ""
        message = error.get("message") or ""
        error_type = error.get("type") or ""
        return f"{code} {error_type}: {message}".strip()
    return str(payload)[:500]


def decode_ark_image_response(session: requests.Session, response: requests.Response) -> Image.Image:
    try:
        result = response.json()
    except Exception as exc:
        raise RuntimeError(f"火山方舟返回不是 JSON: {exc}") from exc

    data = result.get("data") or []
    if not data:
        raise RuntimeError(f"火山方舟返回缺少 data: {str(result)[:500]}")

    first = data[0]
    if first.get("url"):
        downloaded = session.get(first["url"], timeout=120)
        if downloaded.status_code != 200:
            raise RuntimeError(f"下载火山方舟图片失败 HTTP {downloaded.status_code}: {downloaded.text[:200]}")
        return Image.open(BytesIO(downloaded.content)).convert("RGB")
    if first.get("b64_json"):
        img_bytes = base64.b64decode(first["b64_json"])
        return Image.open(BytesIO(img_bytes)).convert("RGB")
    raise RuntimeError(f"火山方舟返回缺少图片内容: {str(first)[:500]}")


def _path_to_data_url(path: str | Path) -> str:
    file_path = Path(path)
    mime_type = mimetypes.guess_type(str(file_path))[0] or "image/png"
    data = base64.b64encode(file_path.read_bytes()).decode("ascii")
    return f"data:{mime_type};base64,{data}"
