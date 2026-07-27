"""Command-line interface for deterministic ExitSpec demonstrations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .authoring import run_define_demo
from .demo_data import support_agent_demo_paths
from .runner import run_demo
from .web import serve_demo


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="exitspec")
    subparsers = parser.add_subparsers(dest="command", required=True)

    demo = subparsers.add_parser(
        "demo", help="Run the deterministic Brick 1 support-agent evidence chain."
    )
    demo.add_argument(
        "--scenario",
        choices=DeterministicToolSelectionAdapter().scenarios,
        default="insufficient",
    )
    demo.add_argument(
        "--contract",
        type=Path,
        default=None,
        metavar="PATH",
        help="Frozen contract path (default: bundled support-agent contract).",
    )
    demo.add_argument(
        "--fixture",
        type=Path,
        default=None,
        metavar="PATH",
        help="Fixture path (default: bundled 200-case support-agent fixture).",
    )
    demo.add_argument("--output-dir", type=Path, default=Path("runs"))
    demo.add_argument("--run-id", type=str, default=None)

    define = subparsers.add_parser(
        "define",
        help="Review source-linked discovery drafts and assemble an approved contract.",
    )
    define.add_argument(
        "--discovery",
        type=Path,
        default=None,
        metavar="PATH",
        help="Discovery pack path (default: bundled synthetic discovery pack).",
    )
    define.add_argument(
        "--review-plan",
        type=Path,
        default=None,
        metavar="PATH",
        help="Review plan path (default: bundled deterministic review plan).",
    )
    define.add_argument(
        "--contract-seed",
        type=Path,
        default=None,
        metavar="PATH",
        help="Contract seed path (default: bundled support-agent contract seed).",
    )
    define.add_argument("--output-dir", type=Path, default=Path("runs"))
    define.add_argument("--session-id", type=str, default=None)

    serve = subparsers.add_parser(
        "serve", help="Run the local synthetic Define → Prove → Decide browser demo."
    )
    serve.add_argument("--host", type=str, default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--output-dir", type=Path, default=Path("runs"))
    serve.add_argument(
        "--open-browser",
        action="store_true",
        help="Open the loopback demo URL after the server starts.",
    )
    serve.add_argument(
        "--enable-fireworks",
        action="store_true",
        help=(
            "Enable the optional frozen synthetic Fireworks action. "
            "Requires FIREWORKS_API_KEY in the server environment."
        ),
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        with support_agent_demo_paths() as demo_paths:
            result = run_demo(
                contract_path=args.contract or demo_paths.frozen_contract,
                fixture_path=args.fixture or demo_paths.fixture,
                scenario=args.scenario,
                output_root=args.output_dir,
                run_id=args.run_id,
            )
        print("Run: {0}".format(result.output_dir))
        print("Criterion verdict: {0}".format(result.criterion_verdict.verdict.value))
        print("Overall verdict: {0}".format(result.overall_verdict.verdict.value))
        return 0
    if args.command == "define":
        with support_agent_demo_paths() as demo_paths:
            result = run_define_demo(
                discovery_path=args.discovery or demo_paths.discovery_pack,
                review_plan_path=args.review_plan or demo_paths.review_plan,
                contract_seed_path=args.contract_seed or demo_paths.contract_seed,
                output_root=args.output_dir,
                session_id=args.session_id,
            )
        print("Define packet: {0}".format(result.output_dir))
        print(
            "Approved contract: {0} v{1}".format(
                result.contract.id, result.contract.version
            )
        )
        return 0
    if args.command == "serve":
        fireworks_api_key = (
            os.environ.get("FIREWORKS_API_KEY")
            if args.enable_fireworks
            else None
        )
        server = serve_demo(
            host=args.host,
            port=args.port,
            output_root=args.output_dir,
            open_browser=args.open_browser,
            enable_fireworks=args.enable_fireworks,
            fireworks_api_key=fireworks_api_key,
        )
        del fireworks_api_key
        print("ExitSpec local demo: http://{0}:{1}".format(args.host, server.server_port))
        provider_status = server.wave1_provider_execution.public_status()
        if provider_status["enabled"] and provider_status["configured"]:
            print(
                "Synthetic-only Fireworks assist enabled. "
                "Every send still requires explicit disclosure acknowledgement."
            )
        elif provider_status["enabled"]:
            print(
                "Fireworks requested but not configured. "
                "The deterministic local path remains available."
            )
        else:
            print(
                "Fireworks disabled. The deterministic local path remains available."
            )
        print("Press Ctrl+C to stop.")
        try:
            server.serve_forever()
        except KeyboardInterrupt:
            pass
        finally:
            server.server_close()
        return 0
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
