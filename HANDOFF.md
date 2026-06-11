# HANDOFF.md — 交接摘要

## 1. 项目目标

微信视频号短剧自动上传工具（ReCreate AI v1.1.0），Python + PyQt5 GUI，PyInstaller 打包发布。有两条流水线：生成流水线（AI改写简介/剧名、转码视频、生成海报、生成成本表模板图）和上传流水线（Patchright隐蔽浏览器自动化填写微信视频号"上架剧集"表单、上传视频、确认提审）。

## 2. 当前功能需求

### 已完成：上传失败保留浏览器窗口
用户反馈上传最后一步失败时浏览器窗口瞬间关闭。已改为失败时保留浏览器窗口让用户手动操作，关闭后程序再继续。

### 已完成：上传卡死修复
上传失败 → 用户手动关闭浏览器后，再次点击"开始上传"无反应（程序卡死）。**根因**：`uploader.py` 的 `context.close()` 在浏览器外部关闭后 playwright CDP WebSocket 断连，调用无限挂起。修复方案：
- `context.close()` / `browser.close()` 包 `asyncio.wait_for(timeout=10)`
- 浏览器轮询循环加 30 分钟上限
- GUI 增加卡死线程自动检测（超时后标记 `_cancelled`，允许重新上传）
- 旧线程 signal 加 `_cancelled` 守卫防止干扰新批次

### 已完成：720p 视频自动放大到 1080p
上传前自动检测视频分辨率，720p（高度 < 1080）的自动用 ffmpeg 放大到 1080p（libx264 ultrafast, 6000k, lanczos 缩放），1080p 及以上不动。原地替换文件。

### 已完成：PyInstaller 打包 + ZIP 交付包
打包成开箱即用的 ZIP（294MB），含 ffmpeg/ffprobe，不含 sucai 素材。产物：`dist/ReCreate_AI_portable.zip`。

## 3. 已完成内容

### ✅ 上传失败保留浏览器窗口
- `uploader.py` 的 `run_task` 方法 finally 块，失败时轮询 `browser.is_connected()` 等待用户关闭
- 加了 30 分钟上限防止无限挂

### ✅ 上传卡死修复
- `uploader.py:477-486` — context/browser 清理加 `asyncio.wait_for(timeout=10)` + 日志
- `gui.py:134-139` — `UploadThread.__init__` 设 `_start_time`（monotonic）和 `_cancelled` 标志
- `gui.py:141-149` — `UploadThread.run()` 发射 signal 前检查 `_cancelled`
- `gui.py:1227-1239` — `run_next_if_idle()` 卡死线程检测（`monotonic` 计算，`_cancelled` 标记，重置批次）
- 已通过代码审查（3 个独立审查者），修复了 2 个 bug + 3 个改进点

### ✅ 720p→1080p 自动放大
- `core/video_processor.py` 新增 `get_video_resolution(path)` 和 `upscale_if_needed(video_files, log_cb)`
- `native_drama_uploader/runner.py:56-59` 上传前调用放大函数
- 放大后文件已为 1080p，重复上传不会重复放大

### ✅ PyInstaller 打包
- `ReCreate AI.spec` 已注释掉 sucai 素材打包
- playwright 和 patchright 都保留

## 4. 已修改/新增文件清单

| 文件 | 改动类型 | 说明 |
|---|---|---|
| `native_drama_uploader/uploader.py` (452-488行) | 修改 | finally 块：轮询加 30 分钟上限，context/browser 清理加 timeout + 日志 |
| `native_drama_uploader/gui.py` (5行, 134-149行, 1227-1262行) | 修改 | UploadThread 加 `_start_time`/`_cancelled`，run_next_if_idle 加卡死检测，import time |
| `native_drama_uploader/runner.py` (56-59行) | 修改 | 上传前调用 upscale_if_needed |
| `core/video_processor.py` (441-527行) | 新增 | get_video_resolution + upscale_if_needed |
| `ReCreate AI.spec` | 修改 | 注释掉 sucai 打包 |

## 5. 关键代码位置

- **上传失败保留浏览器 + 卡死修复**: `uploader.py:452-486` — 轮询上限 + `asyncio.wait_for` timeout 清理
- **卡死线程检测**: `gui.py:1227-1239` — monotonic 计时 + `_cancelled` 标记 + 批次重置
- **UploadThread 守卫**: `gui.py:134-149` — `__init__` 设 `_start_time`/`_cancelled`，`run()` 检查 `_cancelled`
- **720p→1080p 放大**: `core/video_processor.py:441-527` — `get_video_resolution` + `upscale_if_needed`
- **上传前放大调用**: `runner.py:56-59` — `upscale_if_needed(task.video_files)` + 持久化
- **异常传播链**: `uploader.py:run_task` → `runner.py:run_next_task` (68行标记failed) → `gui.py:UploadThread.run` → `gui.py:on_upload_failed`
- **PyInstaller spec**: `ReCreate AI.spec` — 控制打包内容和依赖收集

## 6. 当前报错或未解决问题

无。改动已通过 3 路代码审查，所有已知 bug 已修复。

## 7. 已尝试但失败的方法

- 最初考虑去掉 playwright 只保留 patchright（省103MB），但用户要求保留 playwright，已恢复
- `_start_time` 默认值最初用 `time.time()`，审查发现旧线程会永远不触发卡死检测，改为在 `__init__` 初始化
- 最初 `except Exception: pass` 吞异常无日志，审查发现不利于排查，已加日志

## 8. 当前配置、端口、接口、模型、环境变量

- **Python**: 3.11.9，venv 在 `.venv/`
- **构建工具**: PyInstaller 6.20.0
- **浏览器自动化**: patchright（Playwright 隐蔽分支），fallback 到 playwright
- **Chromium**: 不打包在 ZIP 里，运行时依赖用户系统的 Chrome 或 Edge（`_resolve_chromium_path()` 自动查找）
- **LLM API**: 默认火山引擎豆包（`https://ark.cn-beijing.volces.com/api/v3`），模型 `doubao-seed-2-0-lite-260428`
- **图像 API**: 支持火山方舟/阿里百炼/OpenRouter 三后端
- **许可证服务器**: `http://124.220.63.163:8787`
- **ffmpeg/ffprobe**: 打包在 `ffmpeg/bin/` 下，随交付包分发

## 9. 下一步执行计划

1. **测试验证**: 实际运行 `dist/ReCreate AI/ReCreate AI.exe`，验证：
   - 上传失败时浏览器窗口保留
   - 关闭浏览器后程序正常继续，能再次上传
   - 720p 视频自动放大到 1080p
   - 1080p 视频不被动
2. **git commit**: 提交所有变更

## 10. 固定打包交付逻辑（下次用户说“打包”就按这个做）

### 目标产物
- Windows 免安装目录：`dist/ReCreate AI/`
- ZIP 交付包：`dist/ReCreate_AI_portable.zip`
- 交付包应包含：主程序 `ReCreate AI.exe`、`_internal/` 依赖、`ffmpeg/bin/ffmpeg.exe` 和 `ffmpeg/bin/ffprobe.exe`
- 交付包不包含：`.venv/`、`build/`、`db/`、`cookies/`、`debug/`、`log/`、`sucai` 素材目录、历史 release 目录、源码工作区杂项

### 打包前检查
1. 确认依赖可用：
   ```bash
   python -c "import sys; print(sys.executable)"
   python -c "import PyQt5; print('PyQt5 ok')"
   python -c "import patchright; print('patchright ok')"
   python -c "import openai; print('openai ok')"
   ```
2. 确认 `ffmpeg/bin/` 下有 `ffmpeg.exe`、`ffprobe.exe`（以及需要的 DLL）。
3. 确认 `ReCreate AI.spec` 里：
   - `playwright` 和 `patchright` 都收集；
   - `sucai/视频.docx`、`sucai/模板.jpg` 仍保持注释，不打入发布包；
   - `ffmpeg/bin` 的 exe/dll 通过 `binaries` 打入包。

### 打包命令
```bash
python -m PyInstaller "ReCreate AI.spec" --noconfirm --clean
```

### 生成 ZIP 的标准逻辑
PyInstaller 成功后，以 `dist/ReCreate AI/` 目录为根打包成 `dist/ReCreate_AI_portable.zip`，ZIP 内第一层必须是 `ReCreate AI/` 目录，不能只把内部文件散放到 ZIP 根目录。推荐用 Python 脚本生成，避免 Windows shell 编码/路径问题：

```bash
python - <<'PY'
from pathlib import Path
import zipfile

root = Path('dist/ReCreate AI')
out = Path('dist/ReCreate_AI_portable.zip')
if out.exists():
    out.unlink()
with zipfile.ZipFile(out, 'w', zipfile.ZIP_DEFLATED) as zf:
    for p in root.rglob('*'):
        if p.is_file():
            zf.write(p, p.relative_to(root.parent))
print(out, out.stat().st_size)
PY
```

### 打包后验证
1. 确认 `dist/ReCreate_AI_portable.zip` 存在且大小正常（上次约 294MB）。
2. 检查 ZIP 内容至少包含：
   - `ReCreate AI/ReCreate AI.exe`
   - `ReCreate AI/_internal/...`
   - `ReCreate AI/ffmpeg/bin/ffmpeg.exe`
   - `ReCreate AI/ffmpeg/bin/ffprobe.exe`
3. 如果环境允许，运行 `dist/ReCreate AI/ReCreate AI.exe` 做冒烟验证。
4. `dist/` 和 ZIP 产物被 `.gitignore` 忽略，不提交到 Git；只提交源码、spec、文档等变更。

## 11. 新窗口第一条提示词

```
读 HANDOFF.md 接手。当前所有功能开发已完成，打包逻辑见 HANDOFF.md 第 10 节；交付产物标准路径是 dist/ReCreate_AI_portable.zip。请先检查 git status，再按需要 commit/push。
```
