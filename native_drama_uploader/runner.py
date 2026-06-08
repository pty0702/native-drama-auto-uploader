from __future__ import annotations

from .queue_store import QueueStore
from .settings import load_config
from .license_client import require_valid_license


async def run_next_task(dry_run: bool = False) -> None:
    store = QueueStore()
    store.reset_uploading_to_pending()
    task = store.next_pending()
    if task is None:
        print("没有待上传任务，请先添加成品文件夹到队列。")
        return

    if dry_run:
        print(f"dry-run: 将运行任务 {task.id} {task.drama_name}")
        print(f"文件夹: {task.folder}")
        print(f"视频数: {task.episode_count}")
        print(f"提交审核: {task.submit_after_upload}")
        return

    store.update(task.id, status="uploading", last_error="")
    try:
        config = load_config()
        require_valid_license(config)

        # 上传前检查登录态
        from pathlib import Path
        account_path = Path(config.account_state_path)
        if not account_path.exists():
            raise FileNotFoundError(
                f"未找到登录态文件: {account_path}\n"
                "请先在主界面点击「扫码登录」或运行 python -m native_drama_uploader.cli login"
            )

        # 检查任务文件夹是否存在
        task_folder = Path(task.folder)
        if not task_folder.exists():
            raise FileNotFoundError(
                f"任务文件夹不存在: {task_folder}\n"
                "请确认成品文件夹未被移动或删除。"
            )

        # 检查视频文件是否存在
        missing = [v for v in task.video_files if not Path(v).exists()]
        if missing:
            raise FileNotFoundError(
                f"缺少 {len(missing)} 个视频文件，例如: {missing[0]}\n"
                "请确认成品文件夹内容完整。"
            )

        print(f"开始上传: {task.drama_name} ({task.episode_count}集)")
        print(f"登录态: {account_path}")

        from .uploader import WeChatNativeDramaUploader

        uploader = WeChatNativeDramaUploader(config)
        await uploader.run_task(task)
        store.update(task.id, status="success", last_error="")
        print(f"上传完成: {task.drama_name}")
    except Exception as exc:
        store.update(task.id, status="failed", last_error=str(exc))
        raise
