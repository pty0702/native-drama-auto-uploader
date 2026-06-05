from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import traceback
from pathlib import Path
from typing import Any

from .models import NativeDramaTask
from .settings import AppConfig, DEBUG_DIR

try:
    from patchright.async_api import Browser, BrowserContext, Locator, Page, async_playwright
except ImportError:
    from playwright.async_api import Browser, BrowserContext, Locator, Page, async_playwright


def _resolve_chromium_path() -> str | None:
    """Find the best available Chromium/Chrome executable on this machine.

    Search order:
    1. Playwright / Patchright cache  — ``%LOCALAPPDATA%\\ms-playwright``
       (the exact version the driver expects, installed via
       ``patchright install chromium`` or ``playwright install chromium``)
    2. System Google Chrome
    3. System Microsoft Edge (Chromium-based, present on most Windows 10+)

    When running inside a PyInstaller bundle the driver looks for browsers
    inside the temp extraction directory.  We set ``PLAYWRIGHT_BROWSERS_PATH``
    to point at the real user cache so the cached version can be found.

    Outside PyInstaller, returns ``None`` immediately — Playwright's default
    browser resolution just works.

    Returns the path to a chrome.exe / msedge.exe, or ``None``.
    """
    # Outside PyInstaller, let Playwright use its default behaviour
    if not getattr(sys, "frozen", False):
        return None

    local_app_data = os.environ.get("LOCALAPPDATA", "")

    # ---- 1. Playwright / Patchright cache --------------------------------
    if local_app_data:
        for cache_dir in ("ms-playwright", "patchright"):
            cache_path = os.path.join(local_app_data, cache_dir)
            if not os.path.isdir(cache_path):
                continue
            # Tell the driver to look here
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = cache_path
            # Prefer the newest matching chromium build
            for item in sorted(os.listdir(cache_path), reverse=True):
                if not item.startswith("chromium-"):
                    continue
                exe = os.path.join(cache_path, item, "chrome-win64", "chrome.exe")
                if os.path.isfile(exe):
                    return exe

    # ---- 2. System Google Chrome -----------------------------------------
    for root in (os.environ.get("ProgramFiles", ""),
                 os.environ.get("ProgramFiles(x86)", ""),
                 local_app_data):
        if not root:
            continue
        chrome = os.path.join(root, "Google", "Chrome", "Application", "chrome.exe")
        if os.path.isfile(chrome):
            return chrome

    # ---- 3. System Microsoft Edge ----------------------------------------
    for root in (os.environ.get("ProgramFiles(x86)", ""),
                 os.environ.get("ProgramFiles", "")):
        if not root:
            continue
        edge = os.path.join(root, "Microsoft", "Edge", "Application", "msedge.exe")
        if os.path.isfile(edge):
            return edge

    return None


PLAYLET_URL = "https://channels.weixin.qq.com/platform/playlet"
NATIVE_DRAMA_POST_URL = "https://channels.weixin.qq.com/platform/native-drama-post"


def log(message: str) -> None:
    # 全部写入 debug 日志避免 GBK 终端乱码
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    log_path = DEBUG_DIR / "console.log"
    with open(str(log_path), "a", encoding="utf-8") as f:
        f.write(message + "\n")
    # 尝试打印 ASCII 安全版本到终端
    try:
        print(message, flush=True)
    except (UnicodeEncodeError, UnicodeDecodeError):
        try:
            print(message.encode("ascii", errors="replace").decode("ascii"), flush=True)
        except Exception:
            pass


async def save_screenshot(page: Page, name: str, full_page: bool = False) -> None:
    """截图保存到 debug 目录。默认关闭 full_page 避免复杂页面超时。"""
    DEBUG_DIR.mkdir(parents=True, exist_ok=True)
    path = DEBUG_DIR / name
    try:
        await page.screenshot(path=str(path), full_page=full_page, timeout=15000)
        log(f"截图: {path}")
    except Exception as exc:
        log(f"截图失败({name}): {exc}")


async def save_error(page: Page, step_name: str, exc: BaseException) -> None:
    """保存错误截图和状态，截图失败不影响错误上报。"""
    try:
        await save_screenshot(page, "native_drama_error.png")
    except Exception:
        log("错误截图保存失败")
    try:
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
    except Exception as save_exc:
        log(f"错误状态保存失败: {save_exc}")


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
    # 所有候选都没找到或点击失败
    if not errors:
        await save_screenshot(page, f"safe_click_fail_{step_name}.png")
        raise RuntimeError(f"{step_name}: 未找到任何匹配元素（已截图）")
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
    lower_paths = [path.lower() for path in paths]
    expect_video = any(path.endswith((".mp4", ".mov", ".m4v")) for path in lower_paths)
    expect_non_video = any(
        path.endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp", ".pdf", ".doc", ".docx"))
        for path in lower_paths
    )
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
            accept = (await file_input.get_attribute("accept") or "").lower()
            if expect_video and accept and not any(key in accept for key in ("video", ".mp4", ".mov", ".m4v")):
                continue
            if expect_non_video and accept and any(key in accept for key in ("video", ".mp4", ".mov", ".m4v")):
                continue
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
    try:
        text = await page.locator("body").first.inner_text(timeout=5000)
        (DEBUG_DIR / f"{prefix}_visible_text.txt").write_text(text, encoding="utf-8")
    except Exception as exc:
        log(f"dump_page_state 获取文本失败: {exc}")


class WeChatNativeDramaUploader:
    def __init__(self, config: AppConfig) -> None:
        self.config = config
        self.browser: Browser | None = None
        self.context: BrowserContext | None = None
        self.page: Page | None = None

    async def run_task(self, task: NativeDramaTask) -> None:
        DEBUG_DIR.mkdir(parents=True, exist_ok=True)
        executable_path = _resolve_chromium_path()
        # 总超时：上传超时 + 10分钟缓冲（用于表单填写等）
        overall_timeout = self.config.upload_timeout_min * 60 + 600
        try:
            async with async_playwright() as playwright:
                self.browser = await playwright.chromium.launch(
                    headless=self.config.headless,
                    slow_mo=self.config.browser_slow_mo_ms or None,
                    executable_path=executable_path,
                )
                self.context = await self.browser.new_context(storage_state=self.config.account_state_path)
                await self.context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
                self.page = await self.context.new_page()
                try:
                    log(f"开始上传任务: {task.drama_name} ({task.episode_count}集), 总超时: {overall_timeout}s")
                    await asyncio.wait_for(
                        self._do_upload(task),
                        timeout=overall_timeout,
                    )
                except asyncio.TimeoutError as exc:
                    if type(exc) is not asyncio.TimeoutError:
                        raise
                    log(f"上传任务总超时({overall_timeout}s)，强制结束")
                    raise RuntimeError(f"上传任务超时({overall_timeout // 60}分钟)，请检查网络和页面状态")
                except Exception as exc:
                    await save_error(self.page, "run_task", exc)
                    raise
                finally:
                    delay_sec = max(0, int(getattr(self.config, "close_delay_after_upload_sec", 0) or 0))
                    if delay_sec > 0 and self.page is not None:
                        try:
                            if not self.page.is_closed():
                                log(f"上传流程已结束，窗口保留 {delay_sec} 秒后自动关闭")
                                await self.page.wait_for_timeout(delay_sec * 1000)
                        except Exception as exc:
                            log(f"关闭前等待失败: {exc}")
                    try:
                        await self.context.close()
                    except Exception:
                        pass
                    try:
                        await self.browser.close()
                    except Exception:
                        pass
        except Exception as exc:
            # 确保异常信息清晰，给出排查建议
            error_msg = str(exc)
            if not error_msg:
                error_msg = f"上传失败({type(exc).__name__})"
            if "Target page, context or browser has been closed" in error_msg:
                error_msg = (
                    "浏览器意外关闭，可能原因：\n"
                    "1. 网络不稳定导致页面断开\n"
                    "2. 微信平台会话过期，请重新扫码登录\n"
                    "3. 浏览器被安全软件拦截\n"
                    "4. 系统资源不足\n\n"
                    "建议：重新扫码登录后重试，或重启电脑后再试。"
                )
                log(f"上传任务失败: 浏览器意外关闭")
            elif "Timeout" in error_msg:
                log(f"上传任务超时: {error_msg[:200]}")
            else:
                log(f"上传任务失败: {error_msg[:500]}")
            raise RuntimeError(error_msg) from exc

    async def _do_upload(self, task: NativeDramaTask) -> None:
        """实际的上传流程，由 run_task 包裹超时和错误处理。"""
        await self.open_step1()
        log("步骤1: 页面已打开，开始填写表单")
        await self.fill_step1(task)
        log("步骤1: 表单填写完成")
        await self.click_next()
        await dump_page_state(self.page, "native_drama_step2")
        log("步骤2: 开始上传视频")
        await self.upload_step2(task)
        log("上传任务全部完成")

    async def open_step1(self) -> None:
        assert self.page is not None
        page = self.page
        account = Path(self.config.account_state_path)
        if not account.exists():
            raise FileNotFoundError(f"登录态文件不存在: {account}")
        await page.goto(PLAYLET_URL)
        await settle_page(page, require_networkidle=True)
        if "login" in page.url:
            raise RuntimeError("登录态已失效，当前跳转到了登录页，请重新扫码登录。")
        if "native-drama-post" not in page.url:
            try:
                await wait_for_any_text(page, ("上架剧集", "剧集管理", "收入与服务"), timeout_ms=15000)
            except Exception:
                log("首页未识别到入口文案，尝试直达上架页")
                await page.goto(NATIVE_DRAMA_POST_URL)
                await settle_page(page, require_networkidle=True)
                if "login" in page.url:
                    raise RuntimeError("登录态已失效，直达发布页时跳转到了登录页，请重新扫码登录。")
                if "native-drama-post" in page.url:
                    await settle_page(page)
                    await wheel_back_to_top(page)
                    await wheel_to_text(page, "剧目名称", "wait_step1", max_attempts=20)
                    return
        if "native-drama-post" in page.url:
            await settle_page(page)
            await wheel_back_to_top(page)
            await wheel_to_text(page, "剧目名称", "wait_step1", max_attempts=20)
            return
        await page.wait_for_timeout(2000)
        await safe_click(
            page,
            [
                {"kind": "role_button", "value": "上架剧集"},
                {"kind": "css", "value": 'button:has-text("上架剧集")'},
                {"kind": "css", "value": 'a:has-text("上架剧集")'},
                {"kind": "css", "value": '[class*="btn"]:has-text("上架剧集")'},
                {"kind": "css", "value": 'div:has-text("上架剧集")'},
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
        # 先检查是否有表单错误提示
        body_text = await page.locator("body").first.inner_text(timeout=5000)
        error_keywords = ["不支持的格式", "格式不支持", "格式错误", "格式不正确",
                          "请检查", "上传失败", "不符合要求", "不能为空", "请选择",
                          "校验失败", "提交失败", "参数错误"]
        for kw in error_keywords:
            if kw in body_text:
                await dump_page_state(page, "step1_error")
                # 提取错误信息上下文
                idx = body_text.find(kw)
                context = body_text[max(0,idx-50):idx+100]
                raise RuntimeError(
                    f"表单提交被拒：页面提示「{kw}」。"
                    f"上下文: ...{context}..."
                    f"当前 URL: {page.url}"
                )
        # 检查是否还在 step1（页面还有"剧目名称"等字段说明没跳转）
        if "剧目名称" in body_text and "总集数" in body_text:
            await dump_page_state(page, "step1_next_failed")
            raise RuntimeError(
                "点击下一步后仍在第一页（表单校验可能失败）。"
                f"当前 URL: {page.url}"
            )
        log(f"已进入 step2，URL: {page.url}")

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
                    async with page.expect_file_chooser(timeout=15000) as chooser_info:
                        await target.click(timeout=5000)
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

        await page.wait_for_timeout(2000)
        # 文件选择后，点击「开始上传」/「上传」按钮触发实际上传
        await self._click_start_upload(page)
        all_uploaded = await self.wait_for_all_videos(task.episode_count)
        if not all_uploaded:
            raise RuntimeError(
                f"剧集 {task.drama_name} 视频上传仍有失败（已重试3次），已跳过本剧"
            )
        if task.submit_after_upload:
            await self.confirm_submit()

    async def wait_for_all_videos(self, total: int) -> bool:
        """等待视频全部上传完成，失败时自动重试。返回 True=全部成功, False=跳过本剧"""
        assert self.page is not None
        page = self.page
        deadline = asyncio.get_running_loop().time() + self.config.upload_timeout_min * 60
        stuck_count = 0
        poll_interval = min(self.config.upload_poll_min * 60, 60)  # 轮询最多60秒一次
        poll_count = 0
        retry_count = 0
        max_retries = 3
        while asyncio.get_running_loop().time() < deadline:
            poll_count += 1
            # 截图用于诊断，失败不影响流程
            await save_screenshot(page, "step2_upload_poll.png")
            # 用 JS 获取全部可见文本（包括动态加载、iframe、shadow DOM 内容）
            try:
                text = await page.evaluate("""
                    () => {
                        let result = document.body?.innerText || '';
                        document.querySelectorAll('iframe').forEach(iframe => {
                            try {
                                const doc = iframe.contentDocument || iframe.contentWindow?.document;
                                if (doc && doc.body) result += '\\n' + doc.body.innerText;
                            } catch(e) {}
                        });
                        const walk = (node) => {
                            if (node.shadowRoot) {
                                result += '\\n' + (node.shadowRoot.innerText || node.shadowRoot.textContent || '');
                                node.shadowRoot.childNodes.forEach(walk);
                            }
                            node.childNodes.forEach(walk);
                        };
                        try { document.body.childNodes.forEach(walk); } catch(e) {}
                        return result;
                    }
                """)
            except Exception as exc:
                log(f"获取页面文本失败: {exc}")
                await page.wait_for_timeout(poll_interval * 1000)
                continue
            # 也尝试从所有 frame 中获取文本
            for frame in page.frames:
                try:
                    frame_text = await frame.evaluate(
                        "() => document.body?.innerText || ''"
                    )
                    if frame_text and len(frame_text) > len(text):
                        text = frame_text
                except Exception:
                    pass
            # 保存页面文本用于诊断
            try:
                (DEBUG_DIR / "step2_poll_text.txt").write_text(
                    f"URL: {page.url}\n\n{text}", encoding="utf-8"
                )
            except Exception:
                pass
            try:
                html = await page.content(timeout=10000)
                (DEBUG_DIR / "step2_poll.html").write_text(html, encoding="utf-8")
            except Exception:
                pass
            compact = re.sub(r"\s+", "", text)
            log(f"第 {poll_count} 次轮询 | URL: {page.url} | 文本长度: {len(text)}")
            upload_progress = re.search(r"已上传成功\s*(\d+)\s*/\s*(\d+)\s*集", text)
            done = 0
            expected = total
            if upload_progress:
                done = int(upload_progress.group(1))
                expected = int(upload_progress.group(2))
                log(f"上传进度: {done}/{expected}")
                if done == expected == total:
                    log(f"全部上传成功 {done}/{expected} 集，准备确认提审")
                    return True
            else:
                log(f"未匹配到进度(前200): {text[:200]}")
            # 检测失败/红色状态：查找 "未能上传" "上传失败" 或 "重试" 按钮
            has_failed = bool(re.search(r"未能上传|上传失败|上传异常", text))
            has_retry_btn = bool(re.search(r"重试", text))
            if has_failed or (has_retry_btn and done < total):
                if retry_count < max_retries:
                    retry_count += 1
                    log(f"检测到上传失败条目，点击重试 ({retry_count}/{max_retries})...")
                    await self._click_retry_buttons(page)
                    await page.wait_for_timeout(5000)
                    continue
                else:
                    log(f"已重试 {max_retries} 次，仍有失败条目，跳过本剧")
                    return False
            # 检测是否卡在 0/N（上传没触发），连续3次就报错
            if compact.startswith("0/") or "0/" in compact[:10]:
                stuck_count += 1
                if stuck_count >= 3:
                    raise RuntimeError(
                        f"上传进度连续 {stuck_count} 次为 0/{total}，视频上传可能未触发。"
                        "请检查页面或重试。"
                    )
            else:
                stuck_count = 0
            await page.wait_for_timeout(poll_interval * 1000)
        raise TimeoutError(f"等待视频上传完成超时({self.config.upload_timeout_min}分钟): {total}集")

    async def _click_start_upload(self, page: Page) -> None:
        """文件选择后，查找并点击「开始上传」/「上传」按钮触发上传。"""
        upload_btn_texts = ["开始上传", "上传", "确认上传", "开始"]
        for text in upload_btn_texts:
            try:
                locators = [
                    page.get_by_role("button", name=text),
                    page.locator(f"button:has-text('{text}')"),
                    page.get_by_text(text, exact=False),
                ]
                for locator in locators:
                    count = await locator.count()
                    for i in range(count):
                        btn = locator.nth(i)
                        if await btn.is_visible(timeout=1000):
                            await btn.click(timeout=3000)
                            log(f"已点击「{text}」按钮，触发上传")
                            await page.wait_for_timeout(2000)
                            return
            except Exception:
                continue
        log("未找到「开始上传」按钮，可能自动开始上传")

    async def _click_retry_buttons(self, page: Page) -> None:
        """点击页面上所有可见的「重试」按钮。"""
        retry_locators = [
            page.get_by_role("button", name="重试"),
            page.locator("button:has-text('重试')"),
            page.locator("a:has-text('重试')"),
            page.get_by_text("重试", exact=False),
        ]
        clicked = 0
        for locator in retry_locators:
            try:
                count = await locator.count()
                for i in range(count):
                    btn = locator.nth(i)
                    if await btn.is_visible(timeout=1000):
                        await btn.click(timeout=3000)
                        clicked += 1
                        await page.wait_for_timeout(800)
            except Exception:
                continue
        if clicked > 0:
            log(f"  已点击 {clicked} 个重试按钮")
        else:
            log("  未找到可点击的重试按钮，等待下次轮询")

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
