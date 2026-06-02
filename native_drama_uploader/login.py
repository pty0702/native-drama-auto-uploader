from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

from .settings import AppConfig, DEFAULT_ACCOUNT_STATE_PATH, load_config, save_config

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright


HOME_URL = "https://channels.weixin.qq.com"
PLAYLET_URL = "https://channels.weixin.qq.com/platform/playlet"


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


def _launch_kwargs(headless: bool, executable_path: str | None = None) -> dict:
    kwargs: dict = {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ],
    }
    if executable_path:
        kwargs["executable_path"] = executable_path
    return kwargs


async def login_and_save_state(
    account_state_path: str | Path | None = None,
    timeout_seconds: int = 300,
    headless: bool = False,
) -> Path:
    config = load_config()
    state_path = Path(account_state_path or config.account_state_path or DEFAULT_ACCOUNT_STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    executable_path = _resolve_chromium_path()

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(
            **_launch_kwargs(headless=headless, executable_path=executable_path)
        )
        context = await browser.new_context()
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = await context.new_page()
        try:
            await page.goto(HOME_URL)
            # 等页面完全加载，记下初始 URL
            await page.wait_for_timeout(3000)
            initial_url = page.url
            print(f"登录页: {initial_url}")
            print("请在打开的浏览器里扫码登录微信视频号助手。")
            print(f"登录成功后会自动保存到: {state_path}")
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                url = page.url
                # 核心判断：扫码后页面会跳转，URL 一定会变
                if url != initial_url:
                    print(f"检测到页面跳转: {url}")
                    try:
                        text = await page.locator("body").first.inner_text(timeout=2000)
                    except Exception:
                        text = ""
                    print(f"页面文字前100字: {text[:100]}")
                    try:
                        await page.goto(PLAYLET_URL, wait_until="domcontentloaded", timeout=10000)
                    except Exception:
                        pass
                    await page.wait_for_timeout(2000)
                    await context.storage_state(path=str(state_path))
                    config.account_state_path = str(state_path)
                    save_config(config)
                    print(f"登录态已保存: {state_path}")
                    return state_path
                await page.wait_for_timeout(2000)
            raise TimeoutError(f"扫码登录超时，超过 {timeout_seconds} 秒")
        finally:
            await context.close()
            await browser.close()


def login_blocking(config: AppConfig | None = None) -> Path:
    cfg = config or load_config()
    return asyncio.run(login_and_save_state(cfg.account_state_path, headless=False))
