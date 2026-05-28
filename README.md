# 微信视频号短剧自动上传队列

这是一个全新项目，不修改以下旧项目：

- `G:\python_file\ai_manju5.27`
- `G:\python_file\social-auto-upload`

目标是把 `ai_manju5.27` 生成的短剧成品文件夹加入上传队列，然后由本项目定时或手动上传到微信视频号助手的【收入与服务 -> 剧集管理 -> 上架剧集】流程。

## 成品文件夹规范

每个短剧文件夹建议包含：

```text
短剧名/
  第1集.mp4
  第2集.mp4
  ...
  简介.txt
  海报.jpg
  模版.jpg
  若干证明图片.jpg
```

自动识别规则：

- 剧目名称：文件夹名
- 剧目简介：优先读取 `简介.txt`
- 总集数：统计 `.mp4/.mov/.m4v`
- 试看集数：默认 `5`，GUI 可改
- 制作成本：默认 `1`
- 海报：优先 `海报.jpg`
- 成本模板：优先 `模版.jpg` / `模板.jpg`
- 制作证明材料：除海报和模板外，优先选择文件名包含 `剪影/截图/证明/制作/合同` 的图片，最多 4 张

## 已集成能力

本项目现在包含两条流程：

1. 短剧成品生成流程，来自 `ai_manju5.27` 的核心能力：
   - 扫描源素材文件夹
   - 读取 TXT 简介
   - 调用大模型生成新简介和短剧名
   - 转码/重命名视频
   - 处理海报
   - 生成成本配置模板
   - 输出标准成品文件夹
   - 自动加入上传队列

2. 微信视频号短剧上传流程：
   - 扫码登录并保存登录态
   - 填写【上架剧集】第一页
   - 上传视频
   - 每 3 分钟检查一次进度
   - 全部上传完成后确认提审

## 安装

本机已按 Python 3.11 创建好虚拟环境：

```text
G:\python_file\native_drama_auto_uploader\.venv
```

Python 版本：

```text
Python 3.11.9
```

如需在其他机器重新安装：

```powershell
cd G:\python_file\native_drama_auto_uploader
py -3.11 -m venv .venv
.\.venv\Scripts\pip.exe install -r requirements.txt
.\.venv\Scripts\python.exe -m patchright install chromium
```

还需要系统 PATH 中可用：

```text
ffmpeg
ffprobe
```

本机已检测可用。

## 登录态

本项目可以独立扫码登录并保存登录态，不依赖旧项目。

默认保存到：

```text
G:\python_file\native_drama_auto_uploader\cookies\tencent_uploader\account.json
```

首次使用请先扫码登录：

```powershell
cd G:\python_file\native_drama_auto_uploader
.\.venv\Scripts\python.exe -m native_drama_uploader.cli login
```

也可以打开 GUI 后点击【扫码登录】按钮。登录成功后会自动写入配置文件。

## GUI 使用

双击：

```text
启动界面.bat
```

或命令行运行：

```powershell
cd G:\python_file\native_drama_auto_uploader
.\.venv\Scripts\python.exe main.py
```

界面功能：

- 选择源素材文件夹，执行完整短剧生成流程
- 设置 API Key、API 地址、文本模型、图像模型
- 设置输出目录和成本模板原图
- 生成完成后自动加入上传队列
- 选择监控根目录，默认 `G:\python_file\ai_manju5.27`
- 扫码登录并保存微信视频号助手登录态
- 选择或修改登录态文件路径
- 扫描根目录，把所有符合规范的成品文件夹加入队列
- 手动选择单个成品文件夹加入队列
- 设置制作方名称，并保存为默认
- 设置试看集数，默认 `5`
- 设置制作成本，默认 `1`
- 设置是否上传完成后确认提审
- 手动上传下一条任务
- 定时上传下一条任务

## CLI 使用

扫码登录可以双击：

```text
扫码登录.bat
```

解析一个成品文件夹，不加入队列、不上传：

```powershell
python -m native_drama_uploader.cli dry-run "G:\python_file\ai_manju5.27\骗失忆老公当员工"
```

加入队列：

```powershell
python -m native_drama_uploader.cli add "G:\python_file\ai_manju5.27\骗失忆老公当员工"
```

扫描根目录加入队列：

```powershell
python -m native_drama_uploader.cli scan --root "G:\python_file\ai_manju5.27"
```

查看队列：

```powershell
python -m native_drama_uploader.cli list
```

扫码登录并保存登录态：

```powershell
python -m native_drama_uploader.cli login
```

运行下一条任务：

```powershell
python -m native_drama_uploader.cli run-next
```

只验证下一条任务，不打开浏览器：

```powershell
python -m native_drama_uploader.cli run-next --dry-run
```

## 队列文件

队列保存在：

```text
db/upload_queue.json
```

任务状态：

- `pending`：等待上传
- `uploading`：上传中
- `success`：完成
- `failed`：失败

## 与 ai_manju5.27 联动

第一阶段推荐用“扫描根目录”方式联动：

1. `ai_manju5.27` 生成新短剧文件夹
2. 本项目 GUI 定时扫描 `G:\python_file\ai_manju5.27`
3. 新文件夹进入队列
4. 定时上传器处理 pending 任务

后续可以在 `ai_manju5.27/gui/main_window.py` 的生成完成处追加一条队列写入，这样生成完成后会立刻进入本项目队列。为了保持旧项目原样，本项目当前不直接修改它。

## 上传行为

当前上传器会执行：

1. 打开 `https://channels.weixin.qq.com/platform/playlet`
2. 点击【上架剧集】
3. 填写第一页剧目信息
4. 上传海报、推广海报、证明材料、成本模板
5. 点击【下一步】
6. 第二步选择全部视频文件
7. 每 3 分钟检查一次上传进度
8. 检测到 `已上传成功 N/N 集` 后，点击【确认提审】

如果不想最终提审，可以在 GUI 取消勾选“上传完成后确认提审”，或者 CLI 使用 `--no-submit` 加入任务。

## 调试文件

截图和错误状态保存在：

```text
debug/
```

如果上传失败，优先查看：

- `debug/native_drama_error.png`
- `debug/native_drama_error_state.json`
