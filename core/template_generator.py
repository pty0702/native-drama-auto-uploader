import os
import base64
from datetime import datetime
from io import BytesIO

import httpx
from openai import OpenAI
from PIL import Image


def generate_template_image(
    template_path, output_dir, sub_name, episode_count, total_duration_sec,
    api_key=None, image_model="gpt-image-2",
    api_base_url="https://www.fhl.mom/v1", log_cb=None
):
    """使用 AI 在成本配置图模板上自然填写文字，看不出修改痕迹。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    os.makedirs(output_dir, exist_ok=True)

    total_min = int(total_duration_sec) // 60
    now = datetime.now()
    date_text = f"{now.year}年{now.month}月{now.day}日"

    client = OpenAI(
        api_key=api_key,
        base_url=api_base_url,
        http_client=httpx.Client(trust_env=False, timeout=180),
    )

    prompt = (
        "完全保持原图不变，仅在表格空白处自然补充以下文字信息：\n"
        f"- 微短剧名称处填写：{sub_name}\n"
        f"- 集数和总时长处填写：{episode_count}集，共{total_min}分钟\n"
        f"- 日期处填写：{date_text}\n\n"
        "新增文字要求和原图一致：宋体/仿宋打印字风格，黑灰色墨迹，"
        "匹配纸张纹理、拍照噪点、光照和倾斜透视。"
        "不要改变印章、签字、表格线和任何原有文字。"
        "新增文字要像原本就打印在纸上一样自然。"
    )

    log(f"正在调用 AI 模型({image_model})编辑模板图...")

    # 优先使用 images.edit（真正的图编辑，保留原图内容）
    try:
        with open(template_path, "rb") as f:
            response = client.images.edit(
                model=image_model,
                image=f,
                prompt=prompt,
                n=1,
                response_format="b64_json",
            )
        img_bytes = base64.b64decode(response.data[0].b64_json)
        log("使用 images.edit 编辑完成")
    except Exception:
        # 回退到 images.generate + 参考图
        log("images.edit 不可用，回退到 images.generate...")
        with open(template_path, "rb") as f:
            template_b64 = base64.b64encode(f.read()).decode()

        response = client.images.generate(
            model=image_model,
            prompt=prompt,
            size="2048x2048",
            n=1,
            response_format="b64_json",
            extra_body={
                "image_b64": template_b64,
                "image_strength": 0.3,
            },
        )
        img_bytes = base64.b64decode(response.data[0].b64_json)
        log("使用 images.generate 生成完成")

    img = Image.open(BytesIO(img_bytes))

    out_name = "成本配置图.png"
    out_path = os.path.join(output_dir, out_name)
    img.save(out_path, quality=95)
    log(f"公司资料图(AI)完成: {out_name}")
    return out_path
