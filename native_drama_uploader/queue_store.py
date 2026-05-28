from __future__ import annotations

import json
from pathlib import Path

from .models import NativeDramaTask, now_iso
from .settings import QUEUE_PATH


class QueueStore:
    def __init__(self, path: Path = QUEUE_PATH) -> None:
        self.path = path
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if not self.path.exists():
            self.save([])

    def load(self) -> list[NativeDramaTask]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except Exception:
            raw = []
        return [NativeDramaTask.from_dict(item) for item in raw]

    def save(self, tasks: list[NativeDramaTask]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temp_path = self.path.with_suffix(".tmp")
        temp_path.write_text(
            json.dumps([task.to_dict() for task in tasks], ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        temp_path.replace(self.path)

    def add(self, task: NativeDramaTask, dedupe: bool = True) -> NativeDramaTask:
        tasks = self.load()
        if dedupe:
            existing = next((item for item in tasks if item.folder == task.folder and item.status in {"pending", "uploading"}), None)
            if existing:
                return existing
        tasks.append(task)
        self.save(tasks)
        return task

    def update(self, task_id: str, **changes: object) -> NativeDramaTask:
        tasks = self.load()
        for index, task in enumerate(tasks):
            if task.id != task_id:
                continue
            for key, value in changes.items():
                if hasattr(task, key):
                    setattr(task, key, value)
            task.updated_at = now_iso()
            tasks[index] = task
            self.save(tasks)
            return task
        raise KeyError(f"队列任务不存在: {task_id}")

    def next_pending(self) -> NativeDramaTask | None:
        for task in self.load():
            if task.status == "pending":
                return task
        return None

    def reset_uploading_to_pending(self) -> None:
        tasks = self.load()
        changed = False
        for task in tasks:
            if task.status == "uploading":
                task.status = "pending"
                task.updated_at = now_iso()
                changed = True
        if changed:
            self.save(tasks)
