# AGENTS.md

This file provides guidance to Codex (Codex.ai/code) when working with code in this repository.

## Project Overview

WeChat Video Channel (微信视频号) short drama auto-uploader. Two main pipelines:

1. **Generation Pipeline** — scans source material folders, uses Doubao/volcengine LLM to generate new plot summaries and titles, transcodes videos to H.264 4Mbps (original resolution), generates AI poster images, produces cost-template images, outputs a standard product folder.
2. **Upload Pipeline** — automates the WeChat Channels "上架剧集" flow via stealth browser automation (Patchright/Playwright). Fills drama metadata, uploads videos/posters, polls progress every 3 minutes, optionally confirms submission for review.

Chinese-language codebase: all user-facing strings, comments, batch files, and the README are in Chinese.

## Commands

```bash
# Install dependencies (Python 3.11 + venv)
pip install -r requirements.txt
python -m patchright install chromium

# GUI (also via 启动界面.bat)
python main.py

# CLI subcommands
python -m native_drama_uploader.cli login
python -m native_drama_uploader.cli add "path/to/folder"
python -m native_drama_uploader.cli scan --root "path/to/root"
python -m native_drama_uploader.cli list
python -m native_drama_uploader.cli dry-run "path/to/folder"
python -m native_drama_uploader.cli run-next [--dry-run]
python -m native_drama_uploader.cli config --watch-root "path"
```

External dependencies required on PATH: `ffmpeg`, `ffprobe`.

No test suite exists.

## Architecture

```
main.py                  → GUI entry point (qt_bootstrap fixup → gui.main)
native_drama_uploader/
  cli.py                 → argparse CLI with subcommands: login, add, scan, list, dry-run, run-next, config
  models.py              → NativeDramaTask dataclass + build_task_from_folder() auto-discovery from folder naming conventions
  settings.py            → AppConfig dataclass, persisted at db/config.json
  queue_store.py         → JSON-file queue at db/upload_queue.json; atomic writes; dedup by folder path
  runner.py              → async: picks next pending task → uploader → marks success/failed
  generation_pipeline.py → orchestrates the 4-step generation pipeline (text → video → poster → template)
  gui.py                 → PyQt5 MainWindow (1050×720); threading via QThread subclasses + pyqtSignal
  login.py               → Playwright QR-code login, saves storage_state to cookies/tencent_uploader/account.json
  uploader.py            → WeChatNativeDramaUploader: ~530 lines of browser automation with scroll-based field finding and proximity scoring for DOM elements
  qt_bootstrap.py        → Windows DLL/PATH fixup for PyQt5 runtime conflicts
core/
  text_processor.py      → Doubao LLM via OpenAI-compatible API (ark.cn-beijing.volces.com) for summaries (80-90 chars) and titles (4-8 chars)
  image_processor.py     → AI poster generation with PIL local fallback
  video_processor.py     → FFmpeg transcode to H.264 4Mbps (original resolution), GPU-accelerated, episode rename, MD5 modification
  template_generator.py  → OpenAI image-edit API for cost-report template images
utils/
  file_utils.py          → Video/image extension constants, find_files, episode number extraction, duration helpers
  md5_modifier.py        → Append random bytes to change file MD5
```

### Key Patterns

- **Task queue lifecycle**: `pending` → `uploading` → `success`/`failed`. Queue is a plain JSON array of serialized `NativeDramaTask` dicts.
- **Folder naming conventions** drive auto-discovery: folder name = drama name, `简介.txt` = description, `海报.jpg` = poster, `模版.jpg`/`模板.jpg` = cost template, remaining images with keywords (`剪影/截图/证明/制作/合同`) = proof materials (max 4).
- **Browser automation resilience** (`uploader.py`): WeChat's platform has long dynamic forms. The uploader uses `wheel_to_text` (scroll to find fields), `fill_by_placeholder_after_wheel`, `upload_file_after_wheel` (proximity scoring via JS DOM evaluation), `safe_click` (multiple locator fallbacks), and `enable_ai_statement` (geometric distance to nearest switch). Debug screenshots/state saved to `debug/` on errors.
- **Anti-detection**: uses `patchright` (Playwright stealth fork), overrides `navigator.webdriver`, modifies video file MD5 hashes via `md5_modifier.py`.
- **Dual interface**: GUI (`main.py`) and CLI (`cli.py`) share the same backend modules. GUI uses `threading.Thread` subclasses with `pyqtSignal` for async operations.
- **Config persistence**: `AppConfig` dataclass at `db/config.json`; `load_config()` merges saved values over defaults, auto-migrates legacy paths.
- **AI integration**: all AI calls go through the OpenAI-compatible API at `api_base_url` (default: volcengine/Doubao). Both text generation and image generation use the same API pattern.
