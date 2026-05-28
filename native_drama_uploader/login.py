from __future__ import annotations

import asyncio
from pathlib import Path

from .settings import AppConfig, DEFAULT_ACCOUNT_STATE_PATH, load_config, save_config

try:
    from patchright.async_api import async_playwright
except ImportError:
    from playwright.async_api import async_playwright


HOME_URL = "https://channels.weixin.qq.com"
PLAYLET_URL = "https://channels.weixin.qq.com/platform/playlet"


def _launch_kwargs(headless: bool) -> dict:
    return {
        "headless": headless,
        "args": [
            "--disable-blink-features=AutomationControlled",
            "--disable-infobars",
        ],
    }


async def login_and_save_state(
    account_state_path: str | Path | None = None,
    timeout_seconds: int = 300,
    headless: bool = False,
) -> Path:
    config = load_config()
    state_path = Path(account_state_path or config.account_state_path or DEFAULT_ACCOUNT_STATE_PATH)
    state_path.parent.mkdir(parents=True, exist_ok=True)

    async with async_playwright() as playwright:
        browser = await playwright.chromium.launch(**_launch_kwargs(headless=headless))
        context = await browser.new_context()
        await context.add_init_script("Object.defineProperty(navigator, 'webdriver', {get: () => undefined});")
        page = await context.new_page()
        try:
            await page.goto(HOME_URL)
            print("请在打开的浏览器里扫码登录微信视频号助手。")
            print(f"登录成功后会自动保存到: {state_path}")
            deadline = asyncio.get_running_loop().time() + timeout_seconds
            while asyncio.get_running_loop().time() < deadline:
                try:
                    text = await page.locator("body").inner_text(timeout=2000)
                except Exception:
                    text = ""
                url = page.url
                if any(keyword in text for keyword in ("视频号助手", "首页", "内容管理", "收入与服务")) or "/platform" in url:
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
