import os
import httpx
from openai import OpenAI


def _make_client(api_key):
    return OpenAI(
        api_key=api_key,
        base_url="https://ark.cn-beijing.volces.com/api/v3",
        http_client=httpx.Client(trust_env=False, timeout=60),
    )


def generate_summary_and_name(txt_path, api_key, text_model="doubao-seed-2-0-lite-260428", log_cb=None):
    """读取 TXT，调用火山豆包生成80-90字简介和4-8字副名。"""
    def log(msg):
        if log_cb:
            log_cb(msg)

    with open(txt_path, "r", encoding="utf-8") as f:
        original_text = f.read().strip()

    if not original_text:
        raise ValueError("TXT 文件内容为空")

    client = _make_client(api_key)

    log("正在调用豆包大模型生成简介...")
    resp = client.chat.completions.create(
        model=text_model,
        messages=[
            {"role": "system", "content": "你是一个专业的微短剧内容编辑。"},
            {"role": "user", "content": (
                f"请将以下微短剧简介改写为一个新的简介，要求：\n"
                f"1. 字数严格控制在80到90字之间\n"
                f"2. 语言精炼、有吸引力\n"
                f"3. 不能和原文完全一样，需要重新组织语言\n"
                f"4. 只输出简介内容，不要任何额外说明\n\n"
                f"原文：{original_text}"
            )},
        ],
        temperature=0.8,
    )
    summary = resp.choices[0].message.content.strip()
    log(f"简介生成完成（{len(summary)}字）")

    log("正在生成新副名...")
    resp2 = client.chat.completions.create(
        model=text_model,
        messages=[
            {"role": "system", "content": "你是一个专业的微短剧命名专家。"},
            {"role": "user", "content": (
                f"根据以下微短剧简介，生成一个4到8个字的短剧名称作为副名。\n"
                f"要求：朗朗上口、有吸引力、适合作为短剧标题。\n"
                f"只输出名称，不要任何额外内容（不要引号、不要标点）。\n\n"
                f"简介：{summary}"
            )},
        ],
        temperature=0.9,
    )
    sub_name = resp2.choices[0].message.content.strip().strip('"').strip("'")
    log(f"新副名：{sub_name}")

    return summary, sub_name


def save_results(output_dir, summary, sub_name):
    """保存简介和副名到输出目录，并将目录重命名为副名。"""
    os.makedirs(output_dir, exist_ok=True)

    summary_path = os.path.join(output_dir, "简介.txt")
    with open(summary_path, "w", encoding="utf-8") as f:
        f.write(summary)

    subname_path = os.path.join(output_dir, "副名.txt")
    with open(subname_path, "w", encoding="utf-8") as f:
        f.write(sub_name)

    parent = os.path.dirname(output_dir)
    new_dir = os.path.join(parent, sub_name)
    if os.path.exists(new_dir) and new_dir != output_dir:
        import shutil
        shutil.rmtree(new_dir)
    os.rename(output_dir, new_dir)

    return new_dir, summary_path, subname_path
