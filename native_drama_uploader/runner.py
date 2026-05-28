from __future__ import annotations

from .queue_store import QueueStore
from .settings import load_config


async def run_next_task(dry_run: bool = False) -> None:
    store = QueueStore()
    store.reset_uploading_to_pending()
    task = store.next_pending()
    if task is None:
        print("没有 pending 任务。")
        return

    if dry_run:
        print(f"dry-run: 将运行任务 {task.id} {task.drama_name}")
        print(f"文件夹: {task.folder}")
        print(f"视频数: {task.episode_count}")
        print(f"提交审核: {task.submit_after_upload}")
        return

    config = load_config()
    store.update(task.id, status="uploading", last_error="")
    try:
        from .uploader import WeChatNativeDramaUploader

        uploader = WeChatNativeDramaUploader(config)
        await uploader.run_task(task)
        store.update(task.id, status="success", last_error="")
    except Exception as exc:
        store.update(task.id, status="failed", last_error=str(exc))
        raise
