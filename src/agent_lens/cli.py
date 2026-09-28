"""agent-lens 命令行入口。

P1.3 只落地采集相关命令；`collect --once` 是给测试与手动回填用的单次扫描，
不带 --once 时进入常驻轮询，收到 SIGINT / SIGTERM 后优雅退出（设计文档 10 节）。
"""

from __future__ import annotations

import argparse
import sys


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="agent-lens", description="Codex 会话日志的采集与成本分析"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    collect = sub.add_parser("collect", help="扫描会话日志并入库")
    collect.add_argument("--once", action="store_true", help="只扫描一次后退出")
    collect.add_argument("--config", default=None, help="配置文件路径（TOML）")
    collect.add_argument("--db", default=None, help="覆盖数据库路径")
    collect.add_argument("--sessions-dir", default=None, help="覆盖会话日志目录")
    collect.add_argument("--poll-interval", type=float, default=None, help="轮询间隔（秒）")
    collect.add_argument("--no-langfuse", action="store_true", help="本次运行不上报")

    status = sub.add_parser("status", help="打印数据库与队列概况")
    status.add_argument("--config", default=None)
    status.add_argument("--db", default=None)

    backfill = sub.add_parser("backfill", help="忽略水位，从头重扫全部会话文件")
    backfill.add_argument("--config", default=None)
    backfill.add_argument("--db", default=None)
    backfill.add_argument("--sessions-dir", default=None)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    try:
        args = parser.parse_args(argv)
    except SystemExit as exit_error:
        return int(exit_error.code or 0)
    if args.command == "collect":
        return _run_collect(args)
    if args.command == "status":
        return _run_status(args)
    if args.command == "backfill":
        return _run_backfill(args)
    parser.error(f"未知命令：{args.command}")
    return 2


def _run_collect(args: argparse.Namespace) -> int:
    raise NotImplementedError("Task 8 落地")


def _run_status(args: argparse.Namespace) -> int:
    raise NotImplementedError("Task 8 落地")


def _run_backfill(args: argparse.Namespace) -> int:
    raise NotImplementedError("Task 8 落地")


if __name__ == "__main__":
    sys.exit(main())
