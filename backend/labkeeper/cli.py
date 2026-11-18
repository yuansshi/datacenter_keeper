"""Command line: ``labkeeper serve | demo | import | mcp``."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
import tempfile
from pathlib import Path

from . import __version__


def _settings(args: argparse.Namespace, **extra):
    from .config import configure, load_settings

    overrides = {}
    if getattr(args, "host", None):
        overrides["host"] = args.host
    if getattr(args, "port", None):
        overrides["port"] = args.port
    if getattr(args, "data_dir", None):
        overrides["data_dir"] = args.data_dir
    if getattr(args, "testbeds", None):
        overrides["testbeds"] = {"paths": [str(Path(p).expanduser().resolve()) for p in args.testbeds]}
    overrides.update(extra)
    settings = load_settings(args.config, **overrides)
    if overrides.get("testbeds") and settings.testbeds.paths != overrides["testbeds"]["paths"]:
        settings.testbeds.paths = overrides["testbeds"]["paths"]
    return configure(settings)


def cmd_serve(args: argparse.Namespace, demo: bool = False) -> None:
    import uvicorn

    extra = {"demo": True} if demo else {}
    if demo and not args.data_dir:
        extra["data_dir"] = str(Path(tempfile.gettempdir()) / "labkeeper-demo")
    settings = _settings(args, **extra)
    from .app import create_app

    app = create_app(settings)
    tls = {}
    if settings.tls_certfile and settings.tls_keyfile:
        tls = {"ssl_certfile": str(settings.tls_certfile), "ssl_keyfile": str(settings.tls_keyfile)}
    scheme = "https" if tls else "http"
    print(f"Labkeeper {__version__} on {scheme}://{settings.host}:{settings.port}  (data: {settings.data_dir})")
    if settings.demo:
        print("Demo mode: simulated device status, sample testbeds and fake console servers.")
    uvicorn.run(app, host=settings.host, port=settings.port, log_level=settings.log_level, proxy_headers=True, **tls)


def cmd_import(args: argparse.Namespace) -> None:
    settings = _settings(args)
    from . import db
    from .importer import apply_scan, run_scan
    from .models import Base

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine(settings.resolved_database_url)
    Base.metadata.create_all(db.engine())
    result = run_scan(settings)
    with db.session_scope() as s:
        summary = apply_scan(s, result, settings, actor="cli")
    print(json.dumps(summary.as_dict(), indent=2, default=str))


def cmd_mcp(args: argparse.Namespace) -> None:
    logging.basicConfig(level=logging.WARNING, stream=sys.stderr)
    settings = _settings(args)
    from . import db
    from .agent import Runtime, registry
    from .agent.mcp_server import run_stdio
    from .importer import apply_scan, run_scan
    from .models import Base
    from .providers import Poller, load_providers

    settings.data_dir.mkdir(parents=True, exist_ok=True)
    db.init_engine(settings.resolved_database_url)
    Base.metadata.create_all(db.engine())
    with db.session_scope() as s:
        apply_scan(s, run_scan(settings), settings, actor="mcp")
    runtime = Runtime(settings=settings, poller=Poller(load_providers(settings)))
    asyncio.run(run_stdio(registry, runtime))


def main(argv: list[str] | None = None) -> None:
    parser = argparse.ArgumentParser(prog="labkeeper", description="Testbed inventory and booking for network labs")
    parser.add_argument("--version", action="version", version=__version__)
    sub = parser.add_subparsers(dest="command", required=True)

    def common(p: argparse.ArgumentParser) -> None:
        p.add_argument("-c", "--config", help="path to labkeeper.yaml")
        p.add_argument("--data-dir", help="where the database and uploads live")
        p.add_argument("-t", "--testbeds", action="append", help="testbed YAML folder (repeatable)")

    serve = sub.add_parser("serve", help="run the web app + API + MCP endpoint")
    common(serve)
    serve.add_argument("--host")
    serve.add_argument("--port", type=int)

    demo = sub.add_parser("demo", help="run with sample data and simulated devices")
    common(demo)
    demo.add_argument("--host")
    demo.add_argument("--port", type=int)

    imp = sub.add_parser("import", help="import testbed files once and print a summary")
    common(imp)

    mcp = sub.add_parser("mcp", help="MCP server on stdio (for Claude Code / Claude Desktop)")
    common(mcp)

    args = parser.parse_args(argv)
    if args.command == "serve":
        cmd_serve(args)
    elif args.command == "demo":
        cmd_serve(args, demo=True)
    elif args.command == "import":
        cmd_import(args)
    elif args.command == "mcp":
        cmd_mcp(args)


if __name__ == "__main__":  # pragma: no cover
    main()
