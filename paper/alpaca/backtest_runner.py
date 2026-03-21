from __future__ import annotations

import argparse
from research_runner import (
    DEFAULT_ALPHA_SET,
    DEFAULT_FEED,
    DEFAULT_MAX_PER_FAMILY,
    DEFAULT_PROMOTION_PROFILE,
    build_parser as build_research_parser,
    run_research,
)


def run_backtest(args: argparse.Namespace) -> int:
    return int(run_research(args))


def build_parser() -> argparse.ArgumentParser:
    parser = build_research_parser()
    parser.description = "Run the Alpaca staged research backtest and selection workflow."
    parser.set_defaults(
        alpha_set=DEFAULT_ALPHA_SET,
        feed=DEFAULT_FEED,
        max_candidates_per_family=DEFAULT_MAX_PER_FAMILY,
        promotion_profile=DEFAULT_PROMOTION_PROFILE,
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    return int(run_backtest(args))


if __name__ == "__main__":
    raise SystemExit(main())
