from __future__ import annotations

import asyncio
import json
import re
import traceback
from pathlib import Path
from typing import Any

from .models import NativeDramaTask
from .settings import AppConfig, DEBUG_DIR

try:
    from patchright.async_api import Browser, BrowserContext, Locator, Page, async_playwright
except ImportError:
    from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright


PLAYLET_URL = "https://channels.weixin.qq.com/platform/playlet"
NATIVE_DRAMA_POST_URL = "https://channels.weixin.qq.com/platform/native-drama-post"


def log(message: str) -> None:
    print(message, flush=True)


async def save_screenshot(page: Page, name: str, full_page: bool = True) -> None:
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / name
    await page.screenshot(path=str(path), full_page=full_page)
    log(f"截图: {path}")


async def save_error(page: Page, step_name: str, exc: BaseException) -> None:
    await save_screenshot(page, "native_drama_error.png")
    state = {
        "step_name": step_name,
        "url": page.url,
        "title": await page.title(),
        "error": str(exc),
        "traceback": traceback.format_exc(),
    }
    (DEBUG_DIR / "native_drama_error_state.json").write_text(
        json.dumps(state, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


async def settle_page(page: Page, timeout: int = 6000, require_networkidle: bool = False) -> None:
    try:
        await page.wait_for_load_state("domcontentloaded", timeout=timeout)
    except Exception:
        pass
    if require_networkidle:
        try:
            await page.wait_for_load_state("networkidle", timeout=timeout)
        except Exception:
            pass
    await page.wait_for_timeout(300)


async def wait_for_any_text(page: Page, texts: tuple[str, ...], timeout_ms: int = 8000) -> str:
    deadline = asyncio.get_running_loop().time() + timeout_ms / 1000
    while asyncio.get_running_loop().time() < deadline:
        for text in texts:
            locator = page.get_by_text(text, exact=False).first
            try:
                if await locator.count() and await locator.is_visible(timeout=150):
                    return text
            except Exception:
                continue
        await page.wait_for_timeout(200)
    raise TimeoutError(f"等待页面文本超时: {texts}")


async def wheel_back_to_top(page: Page) -> None:
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    await page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)
    for _ in range(20):
        await page.mouse.wheel(0, -1000)
        await page.wait_for_timeout(80)


async def wheel_to_text(
    page: Page,
    text: str,
    step_name: str,
    max_attempts: int = 40,
    wheel_delta: int = 500,
) -> None:
    log(f"滚轮查找字段: {text}")
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    await page.mouse.move(viewport["width"] / 2, viewport["height"] / 2)
    for _ in range(max_attempts):
        locator = page.get_by_text(text, exact=False).first
        try:
            if await locator.count() and await locator.is_visible(timeout=300):
                await locator.scroll_into_view_if_needed(timeout=2000)
                log(f"找到字段: {text}")
                return
        except Exception:
            pass
        await page.mouse.wheel(0, wheel_delta)
        await page.wait_for_timeout(250)
    await save_screenshot(page, f"wheel_fail_{step_name}.png")
    raise RuntimeError(f"{step_name}: 滚轮查找字段失败: {text}")


async def safe_click(page: Page, candidates: list[dict[str, Any]], step_name: str) -> None:
    errors: list[str] = []
    for candidate in candidates:
        try:
            kind = candidate["kind"]
            value = candidate["value"]
            if kind == "role_button":
                locator = page.get_by_role("button", name=value)
            elif kind == "text":
                locator = page.get_by_text(value, exact=candidate.get("exact", True))
            elif kind == "css":
                locator = page.locator(value)
            else:
                continue
            if not await locator.count():
                continue
            target = locator.first
            await target.scroll_into_view_if_needed(timeout=3000)
            await target.wait_for(state="visible", timeout=5000)
            await target.click(timeout=5000)
            log(f"{step_name}: 点击成功")
            return
        except Exception as exc:
            errors.append(f"{candidate}: {exc}")
    raise RuntimeError(f"{step_name}: 点击失败: {' | '.join(errors)}")


async def fill_by_placeholder_after_wheel(
    page: Page,
    label_text: str,
    placeholder_keyword: str,
    value: str | int,
    step_name: str,
) -> None:
    await wheel_to_text(page, label_text, step_name)
    locator = page.locator(f'input[placeholder*="{placeholder_keyword}"], textarea[placeholder*="{placeholder_keyword}"]')
    await locator.first.wait_for(state="visible", timeout=5000)
    await locator.first.fill(str(value), timeout=5000)
    await page.wait_for_timeout(200)
    log(f"{step_name}: 已填写 {value}")


async def click_text_after_wheel(page: Page, label_text: str, click_text: str, step_name: str) -> None:
    await wheel_to_text(page, label_text, step_name)
    aliases = [click_text]
    if click_text == "IAA广告变现":
        aliases.extend(["IAA广告", "广告变现", "IAA"])
    for alias in aliases:
        locator = page.get_by_text(alias, exact=False)
        for index in range(await locator.count()):
            target = locator.nth(index)
            try:
                if not await target.is_visible(timeout=300):
                    continue
                await target.scroll_into_view_if_needed(timeout=3000)
                await target.click(timeout=5000)
                await page.wait_for_timeout(400)
                log(f"{step_name}: 已点击 {alias}")
                return
            except Exception:
                continue
    raise RuntimeError(f"{step_name}: 未找到可见点击文本: {click_text}")


async def enable_ai_statement(page: Page) -> None:
    await wheel_to_text(page, "AI内容声明", "ai_statement")
    label = page.get_by_text("AI内容声明", exact=False).first
    label_box = await label.bounding_box(timeout=3000)
    if not label_box:
        raise RuntimeError("AI内容声明: 未找到文字位置")
    selectors = [
        '[role="switch"]',
        'input[type="checkbox"]',
        '.weui-desktop-switch',
        '.ant-switch',
        '[class*="switch"]',
        '[class*="Switch"]',
        'button',
    ]
    candidates = page.locator(", ".join(selectors))
    best: tuple[float, Locator] | None = None
    label_y = label_box["y"]
    label_x = label_box["x"]
    viewport = page.viewport_size or {"width": 1280, "height": 720}
    for index in range(await candidates.count()):
        item = candidates.nth(index)
        try:
            if not await item.is_visible(timeout=200):
                continue
            box = await item.bounding_box(timeout=300)
            if not box or box["y"] < 0 or box["y"] > viewport["height"]:
                continue
            if box["y"] < label_y - 80 or box["y"] > label_y + 120:
                continue
            distance = abs(box["y"] - label_y) + abs(box["x"] - label_x) * 0.05
            if best is None or distance < best[0]:
                best = (distance, item)
        except Exception:
            continue
    if best:
        await best[1].click(timeout=3000)
    else:
        await label.click(timeout=3000)
    await page.wait_for_timeout(300)
    log("AI内容声明: 已处理")


async def upload_file_after_wheel(page: Page, label_text: str, files: str | Path | list[str | Path], step_name: str) -> None:
    file_list = files if isinstance(files, list) else [files]
    paths = [str(Path(path)) for path in file_list]
    await wheel_to_text(page, label_text, step_name)
    label = page.get_by_text(label_text, exact=False).first
    label_box = await label.bounding_box(timeout=3000)
    if not label_box:
        raise RuntimeError(f"{step_name}: 未找到 {label_text}")

    trigger_texts = ["选择文件", "上传文件", "上传图片"]
    best: tuple[float, Locator] | None = None
    for text in trigger_texts:
        locators = [page.get_by_role("button", name=text), page.locator(f"button:has-text('{text}')"), page.get_by_text(text, exact=False)]
        for locator in locators:
            for index in range(await locator.count()):
                item = locator.nth(index)
                try:
                    if not await item.is_visible(timeout=200):
                        continue
                    box = await item.bounding_box(timeout=300)
                    if not box or box["y"] < label_box["y"] - 60:
                        continue
                    distance = abs(box["y"] - label_box["y"]) + abs(box["x"] - label_box["x"]) * 0.05
                    if best is None or distance < best[0]:
                        best = (distance, item)
                except Exception:
                    continue
    if best:
        try:
            async with page.expect_file_chooser(timeout=6000) as chooser_info:
                await best[1].click(timeout=5000)
            chooser = await chooser_info.value
            await chooser.set_files(paths)
            await page.wait_for_timeout(800)
            log(f"{step_name}: 已上传 {len(paths)} 个文件")
            return
        except Exception as exc:
            log(f"{step_name}: 按钮上传失败，尝试 file input: {exc}")

    file_inputs = page.locator("input[type='file']")
    best_input: tuple[float, Locator] | None = None
    for index in range(await file_inputs.count()):
        file_input = file_inputs.nth(index)
        try:
            score = await file_input.evaluate(
                """
                (el, args) => {
                  const labelY = args.labelY;
                  let node = el;
                  let bestY = labelY;
                  let hasText = false;
                  for (let depth = 0; depth < 8 && node; depth++) {
                    const text = (node.innerText || node.textContent || '');
                    if (text.includes(args.labelText)) hasText = true;
                    const rect = node.getBoundingClientRect();
                    if (rect.width > 0 && rect.height > 0) {
                      bestY = rect.top;
                      break;
                    }
                    node = node.parentElement;
                  }
                  return Math.abs(bestY - labelY) - (hasText ? 5000 : 0);
                }
                """,
                {"labelText": label_text, "labelY": label_box["y"]},
            )
            if best_input is None or score < best_input[0]:
                best_input = (score, file_input)
        except Exception:
            continue
    if not best_input:
        raise RuntimeError(f"{step_name}: 未找到上传控件")
    await best_input[1].set_input_files(paths, timeout=10000)
    await page.wait_for_timeout(800)
    log(f"{step_name}: 已通过 input 上传 {len(paths)} 个文件")


async def agree_terms(page: Page) -> None:
    await wheel_to_text(page, "我已知悉并同意", "agreement")
    text = page.get_by_text("我已知悉并同意", exact=False).first
    await text.click(timeout=5000)
    await page.wait_for_timeout(300)
    log("已勾选同意")


async def dump_page_state(page: Page, prefix: str) -> None:
    await save_screenshot(page, f"{prefix}_full.png")
    text = await page.locator("body").inner_text(timeout=5000)
    (DEBUG_DIR / f"{prefix}_visible_text.txt").write_text(text, encoding="utf-8")


class WeChatNativeDramaUploader:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def run_task(self, task: NativeDramaTask) -> None:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        async with async_playwright() as playwright:
            self.browser = await playwright.chromium.launch(
                headless=self.config.headless,
                slow_mo=self.config.browser_slow_mo_ms or None,
            )
            self.context = await self.browser.new_context(storage_state=self.config.account_state_path)
            await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
            self.page = await self.context.new_page()
            try:
                await self.open_step1()
                await self.fill_step1(task)
                await self.click_next()
                await dump_page_state(self.page, "native_drama_step2")
                await self.upload_step2(task)
            except Exception as exc:
                await save_error(self.page, "run_task", exc)
                raise
            finally:
                await self.context.close()
                await self.browser.close()

    async def open_step1(self) -> None:
        assert self.page is not None
        page = self.page
        account = Path(self.config.account_state_path)
        if not account.exists():
            raise FileNotFoundError(f"登录态文件不存在: {account}")
        await page.goto(PLAYLET_URL)
        await settle_page(page)
        await wait_for_any_text(page, ("上架剧集", "剧集管理", "收入与服务"), timeout_ms=12000)
        await safe_click(
            page,
            [
                {"kind": "role_button", "value": "上架剧集"},
                {"kind": "css", "value": 'button:has-text("上架剧集")'},
                {"kind": "text", "value": "上架剧集", "exact": True},
            ],
            "点击上架剧集",
        )
        try:
            await page.wait_for_url("**/platform/native-drama-post**", timeout=15000)
        except Exception:
            pass
        await settle_page(page)
        await wheel_back_to_top(page)
        await wheel_to_text(page, "剧目名称", "wait_step1", max_attempts=20)

    async def fill_step1(self, task: NativeDramaTask) -> None:
        assert self.page is not None
        page = self.page
        await fill_by_placeholder_after_wheel(page, "剧目名称", "待提审剧目的名称", task.drama_name, "drama_name")
        await fill_by_placeholder_after_wheel(page, "剧目简介", "介绍相关剧情概要", task.description, "description")
        await fill_by_placeholder_after_wheel(page, "总集数", "总剧集数量", task.episode_count, "episode_count")
        await click_text_after_wheel(page, "变现类型", "IAA广告变现", "monetization")
        await fill_by_placeholder_after_wheel(page, "试看集数", "试看集数", task.trial_episodes, "trial_episodes")
        await click_text_after_wheel(page, "剧目类型", "数字真人", "drama_type")
        await enable_ai_statement(page)
        await upload_file_after_wheel(page, "剧目海报", task.cover_path, "cover")
        await upload_file_after_wheel(page, "推广海报", task.cover_path, "promo_cover")
        await click_text_after_wheel(page, "提审身份", "剧目制作方", "submit_identity")
        await fill_by_placeholder_after_wheel(page, "制作方名称", "制作方主体名称", task.company_name, "company_name")
        if task.proof_images:
            await upload_file_after_wheel(page, "剧目制作证明材料", task.proof_images[:4], "proof_images")
        await click_text_after_wheel(page, "剧目资质", "其他微短剧", "qualification")
        await fill_by_placeholder_after_wheel(page, "剧目资质", "剧目制作成本", task.production_cost, "production_cost")
        if task.template_path:
            for label in ("下载模版", "成本配置比例情况报告", "剧目资质"):
                try:
                    await upload_file_after_wheel(page, label, task.template_path, "template")
                    break
                except Exception as exc:
                    log(f"按 {label} 上传模板失败: {exc}")
            else:
                raise RuntimeError(f"模板文件存在但上传失败: {task.template_path}")
        await agree_terms(page)

    async def click_next(self) -> None:
        assert self.page is not None
        page = self.page
        await wheel_to_text(page, "下一步", "next")
        await safe_click(
            page,
            [
                {"kind": "role_button", "value": "下一步"},
                {"kind": "css", "value": 'button:has-text("下一步")'},
                {"kind": "text", "value": "下一步", "exact": True},
            ],
            "点击下一步",
        )
        await settle_page(page)
        try:
            await wait_for_any_text(page, ("请选择要上传的视频文件", "选择文件", "确认提审"), timeout_ms=10000)
        except Exception:
            pass

    async def upload_step2(self, task: NativeDramaTask) -> None:
        assert self.page is not None
        page = self.page
        paths = [str(Path(item)) for item in task.video_files]
        log("第二步: 进入视频选择快路径")
        uploaded = False

        quick_triggers = [
            page.get_by_role("button", name="选择文件"),
            page.locator("button:has-text('选择文件')"),
            page.get_by_text("选择文件", exact=True),
            page.get_by_text("选择文件", exact=False),
        ]
        for locator in quick_triggers:
            count = await locator.count()
            for index in range(count):
                target = locator.nth(index)
                try:
                    if not await target.is_visible(timeout=150):
                        continue
                    async with page.expect_file_chooser(timeout=5000) as chooser_info:
                        await target.click(timeout=3000)
                    chooser = await chooser_info.value
                    await chooser.set_files(paths)
                    uploaded = True
                    log(f"第二步: 已通过可见【选择文件】按钮选择 {len(paths)} 个视频")
                    break
                except Exception as exc:
                    log(f"第二步: 可见上传按钮未命中，继续尝试: {exc}")
            if uploaded:
                break

        file_inputs = page.locator("input[type='file']")
        if not uploaded:
            for index in range(await file_inputs.count()):
                item = file_inputs.nth(index)
                try:
                    accept = await item.get_attribute("accept") or ""
                    if accept and not any(key in accept.lower() for key in ("video", ".mp4", ".mov", ".m4v")):
                        continue
                    await item.set_input_files(paths, timeout=8000)
                    uploaded = True
                    log(f"第二步: 已通过 input[type=file] 选择 {len(paths)} 个视频")
                    break
                except Exception as exc:
                    log(f"第二步 file input {index} 失败: {exc}")

        if not uploaded:
            for text in ("选择文件", "上传视频", "选择视频", "添加视频"):
                try:
                    await wheel_to_text(page, text, f"step2_{text}", max_attempts=8)
                except Exception:
                    continue
                locator = page.get_by_text(text, exact=False)
                for index in range(await locator.count()):
                    target = locator.nth(index)
                    try:
                        if not await target.is_visible(timeout=300):
                            continue
                        async with page.expect_file_chooser(timeout=8000) as chooser_info:
                            await target.click(timeout=5000)
                        chooser = await chooser_info.value
                        await chooser.set_files(paths)
                        uploaded = True
                        break
                    except Exception:
                        continue
                if uploaded:
                    break
        if not uploaded:
            await save_screenshot(page, "step2_video_select_failed.png")
            raise RuntimeError("第二步没有找到可用的视频上传入口")

        await page.wait_for_timeout(500)
        await self.wait_for_all_videos(task.episode_count)
        if task.submit_after_upload:
            await self.confirm_submit()

    async def wait_for_all_videos(self, total: int) -> None:
        assert self.page is not None
        page = self.page
        deadline = asyncio.get_running_loop().time() + self.config.upload_timeout_min * 60
        while asyncio.get_running_loop().time() < deadline:
            await save_screenshot(page, "step2_upload_poll.png")
            text = await page.locator("body").inner_text(timeout=5000)
            compact = re.sub(r"\s+", "", text)
            if f"{total}/{total}" in compact:
                log(f"第二步: 已上传成功 {total}/{total} 集")
                return
            match = re.search(r"已上传成功\s*(\d+)\s*/\s*(\d+)\s*集", text)
            if match:
                log(f"第二步上传进度: {match.group(1)}/{match.group(2)}")
            else:
                log("第二步上传进度: 未检测到完整进度")
            await page.wait_for_timeout(self.config.upload_poll_min * 60 * 1000)
        raise TimeoutError(f"等待视频上传完成超时: {total}/{total}")

    async def confirm_submit(self) -> None:
        assert self.page is not None
        page = self.page
        await wheel_to_text(page, "确认提审", "confirm_submit", max_attempts=30)
        await safe_click(
            page,
            [
                {"kind": "role_button", "value": "确认提审"},
                {"kind": "css", "value": 'button:has-text("确认提审")'},
                {"kind": "text", "value": "确认提审", "exact": True},
            ],
            "确认提审",
        )
        await page.wait_for_timeout(3000)
        for text in ("确认", "确定", "我知道了"):
            try:
                button = page.get_by_role("button", name=text)
                if await button.count() and await button.first.is_visible(timeout=1000):
                    await button.first.click(timeout=3000)
                    await page.wait_for_timeout(1000)
                    break
            except Exception:
                continue
        await dump_page_state(page, "after_confirm_submit")
