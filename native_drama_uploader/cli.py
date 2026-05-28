from __future__ import annotations

import argparse
import asyncio
from pathlib import Path

from .models import build_task_from_folder
from .queue_store import QueueStore
from .settings import AppConfig, load_config, save_config


def print_task(task) -> None:
    print(f"[{task.status}] {task.id}")
    print(f"  剧目: {task.drama_name}")
    print(f"  文件夹: {task.folder}")
    print(f"  集数: {task.episode_count}")
    print(f"  简介: {task.description}")
    print(f"  海报: {task.cover_path}")
    print(f"  模板: {task.template_path or '未找到'}")
    print(f"  制作方: {task.company_name}")
    print(f"  试看集数: {task.trial_episodes}")
    print(f"  制作成本: {task.production_cost}")


def cmd_add(args: argparse.Namespace) -> None:
    config = load_config()
    task = build_task_from_folder(
        args.folder,
        company_name=args.company or config.default_company_name,
        trial_episodes=args.trial_episodes or config.default_trial_episodes,
        production_cost=args.production_cost or config.default_production_cost,
        submit_after_upload=not args.no_submit,
    )
    added = QueueStore().add(task, dedupe=not args.no_dedupe)
    print_task(added)


def cmd_scan(args: argparse.Namespace) -> None:
    config = load_config()
    root = Path(args.root or config.watch_root)
    if not root.exists():
        raise FileNotFoundError(f"扫描根目录不存在: {root}")
    store = QueueStore()
    added = 0
    for folder in sorted([p for p in root.iterdir() if p.is_dir()], key=lambda p: p.name):
        try:
            task = build_task_from_folder(
                folder,
                company_name=args.company or config.default_company_name,
                trial_episodes=args.trial_episodes or config.default_trial_episodes,
                production_cost=args.production_cost or config.default_production_cost,
                submit_after_upload=not args.no_submit,
            )
            before = len(store.load())
            store.add(task)
            after = len(store.load())
            if after > before:
                added += 1
                print(f"加入队列: {folder}")
        except Exception as exc:
            if args.verbose:
                print(f"跳过 {folder}: {exc}")
    print(f"扫描完成，新增 {added} 个任务。")


def cmd_list(_: argparse.Namespace) -> None:
    tasks = QueueStore().load()
    if not tasks:
        print("队列为空。")
        return
    for task in tasks:
        print_task(task)


def cmd_config(args: argparse.Namespace) -> None:
    config = load_config()
    changed = False
    for key in (
        "watch_root",
        "account_state_path",
        "default_company_name",
        "default_trial_episodes",
        "default_production_cost",
        "upload_interval_min",
        "submit_after_upload",
    ):
        value = getattr(args, key, None)
        if value is not None:
            setattr(config, key, value)
            changed = True
    if changed:
        save_config(config)
    print(config)


def cmd_dry_run(args: argparse.Namespace) -> None:
    config = load_config()
    task = build_task_from_folder(
        args.folder,
        company_name=args.company or config.default_company_name,
        trial_episodes=args.trial_episodes or config.default_trial_episodes,
        production_cost=args.production_cost or config.default_production_cost,
        submit_after_upload=not args.no_submit,
    )
    print_task(task)


def cmd_run_next(args: argparse.Namespace) -> None:
    async def _run() -> None:
        from .runner import run_next_task

        await run_next_task(dry_run=args.dry_run)

    asyncio.run(_run())


def cmd_login(args: argparse.Namespace) -> None:
    async def _login() -> None:
        from .login import login_and_save_state

        await login_and_save_state(
            account_state_path=args.account_state_path,
            timeout_seconds=args.timeout,
            headless=False,
        )

    asyncio.run(_login())


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="微信视频号短剧自动上传队列")
    sub = parser.add_subparsers(dest="cmd", required=True)

    add = sub.add_parser("add", help="把一个成品文件夹加入上传队列")
    add.add_argument("folder")
    add.add_argument("--company")
    add.add_argument("--trial-episodes", type=int)
    add.add_argument("--production-cost", type=int)
    add.add_argument("--no-submit", action="store_true")
    add.add_argument("--no-dedupe", action="store_true")
    add.set_defaults(func=cmd_add)

    scan = sub.add_parser("scan", help="扫描根目录，将成品文件夹加入队列")
    scan.add_argument("--root")
    scan.add_argument("--company")
    scan.add_argument("--trial-episodes", type=int)
    scan.add_argument("--production-cost", type=int)
    scan.add_argument("--no-submit", action="store_true")
    scan.add_argument("--verbose", action="store_true")
    scan.set_defaults(func=cmd_scan)

    ls = sub.add_parser("list", help="查看队列")
    ls.set_defaults(func=cmd_list)

    dry = sub.add_parser("dry-run", help="只解析文件夹，不加入队列，不上传")
    dry.add_argument("folder")
    dry.add_argument("--company")
    dry.add_argument("--trial-episodes", type=int)
    dry.add_argument("--production-cost", type=int)
    dry.add_argument("--no-submit", action="store_true")
    dry.set_defaults(func=cmd_dry_run)

    cfg = sub.add_parser("config", help="查看或修改配置")
    cfg.add_argument("--watch-root")
    cfg.add_argument("--account-state-path")
    cfg.add_argument("--default-company-name")
    cfg.add_argument("--default-trial-episodes", type=int)
    cfg.add_argument("--default-production-cost", type=int)
    cfg.add_argument("--upload-interval-min", type=int)
    cfg.add_argument("--submit-after-upload", type=lambda v: v.lower() in {"1", "true", "yes", "y"})
    cfg.set_defaults(func=cmd_config)

    run = sub.add_parser("run-next", help="运行队列里的下一条任务")
    run.add_argument("--dry-run", action="store_true")
    run.set_defaults(func=cmd_run_next)

    login = sub.add_parser("login", help="扫码登录微信视频号助手并保存登录态")
    login.add_argument("--account-state-path")
    login.add_argument("--timeout", type=int, default=300)
    login.set_defaults(func=cmd_login)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
