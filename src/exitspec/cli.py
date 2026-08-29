"""Command-line interface for deterministic ExitSpec demonstrations."""

from __future__ import annotations

import argparse
import os
from pathlib import Path
from typing import Optional, Sequence

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .authoring import run_define_demo
from .demo_data import support_agent_demo_paths
from .performance_operations import PerformanceOperationStatus
from .performance_runner import run_performance_proof
from .runner import run_demo
from .poc_source_demo import serve_source_neutral_demo
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

    performance = subparsers.add_parser(
        "performance",
        help=(
            "Run one frozen inference-performance proof against an "
            "OpenAI-compatible streaming endpoint."
        ),
    )
    performance.add_argument(
        "--contract",
        type=Path,
        required=True,
        metavar="PATH",
        help="Customer-confirmed frozen performance contract JSON.",
    )
    performance.add_argument(
        "--confirmation",
        type=Path,
        required=True,
        metavar="PATH",
        help="Matching customer confirmation JSON.",
    )
    performance.add_argument(
        "--bundle-root",
        type=Path,
        required=True,
        metavar="PATH",
        help="Root containing the contract-bound workload and prompts.",
    )
    performance.add_argument(
        "--idempotency-key",
        type=str,
        required=True,
        help="Stable operation key; never persisted in raw form.",
    )
    performance.add_argument("--output-dir", type=Path, default=Path("runs"))
    performance.add_argument(
        "--operation-db",
        type=Path,
        default=None,
        metavar="PATH",
        help="Durable SQLite ledger (default: inside output-dir).",
    )
    performance.add_argument(
        "--api-key-env",
        type=str,
        default=None,
        metavar="NAME",
        help=(
            "Read an endpoint credential from this environment variable. "
            "Do not place API keys directly on the command line."
        ),
    )
    performance.add_argument(
        "--credential-endpoint",
        type=str,
        default=None,
        metavar="URL",
        help=(
            "Exact frozen endpoint authorized to receive the credential. "
            "Required with --api-key-env."
        ),
    )
    performance.add_argument(
        "--authorize-requests",
        type=int,
        default=None,
        metavar="COUNT",
        help=(
            "Authorize the exact preflight + warmup + measured request count. "
            "Required for remote or credentialed execution."
        ),
    )

    serve = subparsers.add_parser(
        "serve",
        help=(
            "Run the local synthetic Capture → Review → Agree → Prove → "
            "Decide browser demo."
        ),
    )
    serve.add_argument("--host", type=str, default="127.0.0.1")
    serve.add_argument("--port", type=int, default=8765)
    serve.add_argument("--output-dir", type=Path, default=Path("runs"))
    serve.add_argument(
        "--source-neutral",
        action="store_true",
        help=(
            "Run the source-neutral A2 local intake demo without the seeded "
            "support-agent session or downstream proof routes."
        ),
    )
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
    serve.add_argument(
        "--enable-fireworks-stt",
        action="store_true",
        help=(
            "Send one consented synthetic browser clip to the pinned "
            "experimental Fireworks Whisper v3 transport. Requires "
            "FIREWORKS_API_KEY in the server environment."
        ),
    )
    serve.add_argument(
        "--inferdrome-runs-root",
        type=Path,
        default=None,
        metavar="PATH",
        help=(
            "Enable pathless import from sealed, customer-eligible bundles "
            "beneath this explicit local Inferdrome runs root."
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
    if args.command == "performance":
        api_key = (
            os.environ.get(args.api_key_env)
            if args.api_key_env is not None
            else None
        )
        if args.api_key_env is not None and api_key is None:
            raise ValueError(
                "The requested API key environment variable is not set."
            )
        if args.api_key_env is not None and args.credential_endpoint is None:
            raise ValueError(
                "--credential-endpoint is required with --api-key-env."
            )
        if args.api_key_env is None and args.credential_endpoint is not None:
            raise ValueError(
                "--credential-endpoint requires --api-key-env."
            )
        result = run_performance_proof(
            contract_path=args.contract,
            confirmation_path=args.confirmation,
            bundle_root=args.bundle_root,
            output_root=args.output_dir,
            operation_database_path=args.operation_db,
            idempotency_key=args.idempotency_key,
            api_key=api_key,
            credential_endpoint=args.credential_endpoint,
            authorized_request_count=args.authorize_requests,
        )
        del api_key
        print("Operation: {0}".format(result.operation.run_id))
        print("Execution status: {0}".format(result.operation.status.value))
        if result.verdict is not None and result.artifacts is not None:
            print("Evidence verdict: {0}".format(result.verdict.value))
            print(
                "Evidence Pack: {0}".format(
                    result.artifacts.run_dir / "decision-packet.html"
                )
            )
            return 0 if result.verdict.value == "PASS" else 2
        if result.operation.status in {
            PerformanceOperationStatus.BLOCKED,
            PerformanceOperationStatus.NOT_PROVEN,
        }:
            print(
                "Reason: {0}".format(
                    result.operation.terminal_reason or "NOT_PROVEN"
                )
            )
            return 3
        return 4
    if args.command == "serve":
        if args.source_neutral:
            incompatible = []
            if args.enable_fireworks:
                incompatible.append("--enable-fireworks")
            if args.enable_fireworks_stt:
                incompatible.append("--enable-fireworks-stt")
            if args.inferdrome_runs_root is not None:
                incompatible.append("--inferdrome-runs-root")
            if incompatible:
                raise ValueError(
                    "--source-neutral cannot be combined with {0}.".format(
                        ", ".join(incompatible)
                    )
                )
            server = serve_source_neutral_demo(
                host=args.host,
                port=args.port,
                open_browser=args.open_browser,
            )
            print(
                "ExitSpec source-neutral A2 demo: "
                "http://{0}:{1}/app/pocs/new".format(
                    args.host,
                    server.server_port,
                )
            )
            print("Process-local source intake only. Press Ctrl+C to stop.")
            try:
                server.serve_forever()
            except KeyboardInterrupt:
                pass
            finally:
                server.server_close()
            return 0
        fireworks_api_key = (
            os.environ.get("FIREWORKS_API_KEY")
            if args.enable_fireworks or args.enable_fireworks_stt
            else None
        )
        server = serve_demo(
            host=args.host,
            port=args.port,
            output_root=args.output_dir,
            open_browser=args.open_browser,
            enable_fireworks=args.enable_fireworks,
            fireworks_api_key=(
                fireworks_api_key if args.enable_fireworks else None
            ),
            enable_fireworks_stt=args.enable_fireworks_stt,
            fireworks_stt_api_key=(
                fireworks_api_key if args.enable_fireworks_stt else None
            ),
            inferdrome_runs_root=(
                None
                if args.inferdrome_runs_root is None
                else args.inferdrome_runs_root.resolve()
            ),
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
        stt_runtime = getattr(server, "stt_demo_runtime", None)
        if getattr(stt_runtime, "live_provider_enabled", False):
            print(
                "Experimental Fireworks STT enabled for consented synthetic "
                "browser clips. Transcripts remain untrusted and review-only."
            )
        elif args.enable_fireworks_stt:
            print(
                "Fireworks STT requested but not configured. Paste transcript "
                "and fixed synthetic recording remain available."
            )
        if args.inferdrome_runs_root is not None:
            print(
                "Inferdrome import enabled for the explicit local runs root. "
                "ExitSpec still verifies and recalculates every selected bundle."
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
