"""Command-line interface for deterministic ExitSpec demonstrations."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Optional, Sequence

from .adapters.deterministic_tool_selection import DeterministicToolSelectionAdapter
from .authoring import run_define_demo
from .runner import run_demo


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
        default=Path("examples/support-agent/contracts/tool-selection-v1.yaml"),
    )
    demo.add_argument(
        "--fixture",
        type=Path,
        default=Path("examples/support-agent/fixtures/tool-selection-200.json"),
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
        default=Path("examples/support-agent/authoring/discovery-pack-v1.json"),
    )
    define.add_argument(
        "--review-plan",
        type=Path,
        default=Path("examples/support-agent/authoring/review-plan-v1.json"),
    )
    define.add_argument(
        "--contract-seed",
        type=Path,
        default=Path("examples/support-agent/authoring/contract-seed-v1.json"),
    )
    define.add_argument("--output-dir", type=Path, default=Path("runs"))
    define.add_argument("--session-id", type=str, default=None)
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    if args.command == "demo":
        result = run_demo(
            contract_path=args.contract,
            fixture_path=args.fixture,
            scenario=args.scenario,
            output_root=args.output_dir,
            run_id=args.run_id,
        )
        print("Run: {0}".format(result.output_dir))
        print("Criterion verdict: {0}".format(result.criterion_verdict.verdict.value))
        print("Overall verdict: {0}".format(result.overall_verdict.verdict.value))
        return 0
    if args.command == "define":
        result = run_define_demo(
            discovery_path=args.discovery,
            review_plan_path=args.review_plan,
            contract_seed_path=args.contract_seed,
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
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
