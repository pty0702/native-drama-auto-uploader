import os
import base64
import httpx
from openai import OpenAI
from PIL import Image, ImageDraw, ImageFont
from io import BytesIO


def process_images(image_files, output_dir, sub_name, api_key,
                   image_model="doubao-seedream-5-0-260128",
                   target_w=816, target_h=1086, log_cb=None):
    """对每张海报图片：先用豆包视觉模型描述原图，再用生图模型生成新海报。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    os.makedirs(output_dir, exist_ok=True)
    results = []

    client = OpenAI(
        api_key=api_key,
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        http_client=httpx.Client(trust_env=False, timeout=120),
    )

    for i, img_path in enumerate(image_files, 1):
        log(f"正在处理海报图 {i}/{len(image_files)}: {os.path.basename(img_path)}")

        try:
            new_img = _generate_poster(client, img_path, sub_name, image_model, log)
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

        new_img.save(out_path, quality=95)
        log(f"  完成: {out_name}")
        results.append(out_path)

    return results


def _generate_poster(client, img_path, sub_name, image_model, log):
    """用生图模型生成带有新副名的海报。"""
    # 读取原图并编码
    with open(img_path, "rb") as f:
        img_b64 = base64.b64encode(f.read()).decode()

    # 构建生图 prompt：基于短剧海报风格 + 新副名
    prompt = (
        f"一张微短剧竖版海报，顶部大字标题「{sub_name}」，"
        f"现代都市爱情风格，高质量设计，精美排版，"
        f"深色背景配金色或白色艺术字体，电影海报质感，816x1086比例"
    )

    log(f"  正在调用生图模型({image_model})...")
    response = client.images.generate(
        model=image_model,
        prompt=prompt,
        size="2048x2048",
        n=1,
        response_format="b64_json",
    )

    img_b64_result = response.data[0].b64_json
    img_bytes = base64.b64decode(img_b64_result)
    img = Image.open(BytesIO(img_bytes))
    return img.resize((816, 1086), Image.LANCZOS)


def _replace_text_local(img_path, sub_name, target_w=816, target_h=1086):
    """本地备选方案：在图片上覆盖绘制新副名。"""
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
