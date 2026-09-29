"""agent-lens 命令行入口。

`collect --once` 是单次扫描，给测试与手动回填用；不带 --once 时进入常驻轮询，
收到 SIGINT / SIGTERM 后停止扫描、把队列里已入队的记录发完再退出（设计文档 10 节）。
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
from pathlib import Path

from .collector import Collector, CollectOutcome
from .config import AppConfig, load_config
from .redact import Redactor
from .reporter import (
    LangfuseSink,
    LangfuseTraceBackend,
    Reporter,
    ReportOutcome,
    ReportQueue,
)
from .storage import connect, counts, init_db, write_context_breakdown, write_skill_hits


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

    serve = sub.add_parser("serve", help="启动查询 API（并托管前端构建产物）")
    serve.add_argument("--host", default="127.0.0.1", help="监听地址")
    serve.add_argument("--port", type=int, default=8000, help="监听端口")
    serve.add_argument("--reload", action="store_true", help="开发模式自动重载")
    serve.add_argument("--config", default=None)
    serve.add_argument("--db", default=None)
    serve.add_argument("--web-dir", default=None, help="前端构建产物目录，默认 web/dist")

    analyze = sub.add_parser("analyze", help="重放会话文件，计算上下文构成")
    analyze.add_argument("--config", default=None)
    analyze.add_argument("--db", default=None)
    analyze.add_argument("--sessions-dir", default=None)
    analyze.add_argument("--file", action="append", default=None, help="只分析指定文件，可重复")

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
    if args.command == "serve":
        return _run_serve(args)
    if args.command == "analyze":
        return _run_analyze(args)
    parser.error(f"未知命令：{args.command}")
    return 2


def _run_collect(args: argparse.Namespace) -> int:
    config, conn = _build_runtime(args)
    reporter, queue = _build_reporter(conn, config)
    if args.no_langfuse:
        reporter, queue = None, None
    collector = Collector(
        conn,
        sessions_dir=config.sessions_dir,
        queue=queue,
        redactor=Redactor(enabled=config.redaction.enabled),
        granularity=config.langfuse.granularity,
        summary_chars=config.redaction.summary_chars,
    )
    try:
        if args.once:
            outcome = collector.scan_once()
            report = reporter.drain_once() if reporter is not None else None
            print(_format_outcome(outcome, report))
            return 0

        stop_event = threading.Event()
        _install_signal_handlers(stop_event)
        print(
            f"collecting from {config.sessions_dir} every "
            f"{config.sessions.poll_interval_seconds}s (Ctrl-C to stop)",
            flush=True,
        )
        collector.run(
            poll_interval=config.sessions.poll_interval_seconds,
            stop_event=stop_event,
            on_scan=lambda outcome: print(_format_outcome(outcome, None), flush=True),
        )
        if reporter is not None:
            print(_format_outcome(CollectOutcome(), reporter.drain_once()))
        return 0
    finally:
        conn.close()


def _run_status(args: argparse.Namespace) -> int:
    config, conn = _build_runtime(args, with_sessions_dir=False)
    try:
        for table, count in counts(conn).items():
            print(f"{table}: {count}")
        rows = conn.execute(
            "SELECT status, COUNT(*) AS n FROM report_queue GROUP BY status"
        ).fetchall()
        for row in rows:
            print(f"report_queue[{row['status']}]: {row['n']}")
        print(f"db: {config.db_path}")
        print(f"sessions: {config.sessions_dir}")
        return 0
    finally:
        conn.close()


def _run_backfill(args: argparse.Namespace) -> int:
    """把水位归零后重扫。幂等键保证不会产生重复行，只是重算一遍。"""
    config, conn = _build_runtime(args)
    try:
        conn.execute("UPDATE ingest_state SET byte_offset = 0, file_size = 0, mtime = 0")
        conn.commit()
        collector = Collector(
            conn,
            sessions_dir=config.sessions_dir,
            redactor=Redactor(enabled=config.redaction.enabled),
            summary_chars=config.redaction.summary_chars,
        )
        print(_format_outcome(collector.scan_once(), None))
        return 0
    finally:
        conn.close()


def _build_runtime(args: argparse.Namespace, *, with_sessions_dir: bool = True):
    """加载配置、套用命令行覆盖、打开并建库。"""
    config = load_config(args.config)
    if getattr(args, "db", None):
        config.storage.db_path = args.db
    if with_sessions_dir and getattr(args, "sessions_dir", None):
        config.sessions.dir = args.sessions_dir
    if getattr(args, "poll_interval", None) is not None:
        config.sessions.poll_interval_seconds = args.poll_interval
    conn = connect(config.db_path)
    init_db(conn)
    return config, conn


def _build_reporter(conn, config: AppConfig) -> tuple[Reporter | None, ReportQueue | None]:
    """按配置组装上报链路。未启用上报时返回 (None, None)——不上报就不入队。"""
    if not config.langfuse.enabled:
        return None, None
    queue = ReportQueue(conn)
    backend = LangfuseTraceBackend.from_keys(
        public_key=config.langfuse.public_key or "",
        secret_key=config.langfuse.secret_key or "",
        host=config.langfuse.host,
    )
    reporter = Reporter(
        queue,
        LangfuseSink(backend),
        batch_size=config.langfuse.batch_size,
        max_requests_per_minute=config.langfuse.max_requests_per_minute,
    )
    return reporter, queue


def _install_signal_handlers(stop_event: threading.Event) -> None:
    """SIGINT / SIGTERM 只置位，不打断正在进行的扫描与写库。"""

    def _handle(_signum, _frame):
        stop_event.set()

    for name in ("SIGINT", "SIGTERM"):
        number = getattr(signal, name, None)
        if number is None:
            continue
        try:
            signal.signal(number, _handle)
        except ValueError:
            pass  # 非主线程调用时跳过，不影响主流程


def _format_outcome(outcome: CollectOutcome, report: ReportOutcome | None) -> str:
    text = (
        f"scanned={outcome.files_scanned} changed={outcome.files_changed} "
        f"lines={outcome.lines_read} api_calls={outcome.api_calls_written} "
        f"tool_calls={outcome.tool_calls_written} queued={outcome.reports_queued} "
        f"parse_errors={outcome.parse_errors}"
    )
    if report is not None:
        text += f" sent={report.sent} failed={report.failed} pending={report.pending}"
    return text


def _run_analyze(args: argparse.Namespace) -> int:
    """重放会话文件并写入上下文分解。不联网、不改事实源（只读 JSONL）。"""
    from . import context as context_module
    from . import skills as skills_module

    config, conn = _build_runtime(args)
    try:
        if args.file:
            paths = [Path(item) for item in args.file]
        else:
            paths = sorted(Path(config.sessions_dir).rglob("*.jsonl"))
        calls = 0
        written = 0
        skills_found = 0
        for path in paths:
            breakdowns = context_module.decompose_file(path)
            if breakdowns:
                calls += len(breakdowns)
                written += write_context_breakdown(conn, breakdowns)
            skills_found += write_skill_hits(conn, skills_module.find_skill_hits_in_file(path))
        print(f"analyzed={len(paths)} calls={calls} blocks={written} skills={skills_found}")
        return 0
    finally:
        conn.close()


def _run_serve(args: argparse.Namespace) -> int:
    """启动 uvicorn。--reload 走工厂字符串，普通模式直接传 app 实例。"""
    import uvicorn

    from .api.app import create_app

    config = load_config(args.config)
    if args.db:
        config.storage.db_path = args.db
    if args.reload:
        # reload 需要 uvicorn 自己重新导入；数据库路径走 AGENT_LENS_DB 环境变量。
        import os

        os.environ.setdefault("AGENT_LENS_DB", str(config.db_path))
        uvicorn.run(
            "agent_lens.api.app:create_app", factory=True, host=args.host, port=args.port, reload=True
        )
        return 0

    web_dir = Path(args.web_dir) if args.web_dir else Path("web/dist")
    app = create_app(
        config.db_path, static_dir=web_dir if web_dir.exists() else None
    )
    if web_dir.exists():
        print(f"serving API + frontend from {web_dir} on http://{args.host}:{args.port}")
    else:
        print(f"serving API only on http://{args.host}:{args.port} (no {web_dir})")
    uvicorn.run(app, host=args.host, port=args.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
