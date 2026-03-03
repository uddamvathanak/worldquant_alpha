from __future__ import annotations

import argparse
from datetime import datetime
import json
from pathlib import Path
import time
import uuid

import pandas as pd

from broker_alpaca import AlpacaBroker
from config import ET, load_config
from tracker import PaperTracker


def _stamp_now_et() -> str:
    return datetime.now(ET).strftime("%Y%m%d_%H%M%S")


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Close all Alpaca paper positions on demand.",
    )
    parser.add_argument(
        "--yes",
        action="store_true",
        help="Confirm live liquidation. Required unless --dry-run is used.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Preview positions to close without sending liquidation orders.",
    )
    parser.add_argument(
        "--no-cancel-open-orders",
        action="store_true",
        help="Do not cancel open orders before/while liquidating.",
    )
    parser.add_argument(
        "--timeout-seconds",
        type=int,
        default=90,
        help="Maximum wait time for positions to flatten after submission.",
    )
    parser.add_argument(
        "--poll-seconds",
        type=int,
        default=3,
        help="Polling interval while waiting for flatten confirmation.",
    )
    return parser


def _write_liquidation_csvs(
    *,
    logs_dir: Path,
    stamp: str,
    positions_pre: pd.DataFrame,
    positions_post: pd.DataFrame,
    responses: pd.DataFrame,
) -> None:
    positions_pre.to_csv(logs_dir / f"liquidation_positions_pre_{stamp}.csv", index=False)
    positions_post.to_csv(logs_dir / f"liquidation_positions_post_{stamp}.csv", index=False)
    responses.to_csv(logs_dir / f"liquidation_orders_{stamp}.csv", index=False)


def main() -> int:
    args = _build_parser().parse_args()
    cfg = load_config()
    tracker = PaperTracker(cfg.db_path, cfg.logs_dir)

    run_id = uuid.uuid4().hex[:12]
    trade_date = datetime.now(ET).date()
    signal_path = cfg.base_dir / "MANUAL_LIQUIDATION"

    tracker.log_run_start(
        run_id=run_id,
        trade_date=trade_date,
        signal_path=signal_path,
        config=cfg.to_public_dict(),
        status="manual_liquidation_started",
    )

    try:
        api_key, api_secret = cfg.require_alpaca_credentials()
        broker = AlpacaBroker(api_key, api_secret, paper=True)

        account_pre = broker.get_account_snapshot()
        positions_pre = broker.list_positions()
        tracker.log_account_snapshot(run_id=run_id, snapshot_stage="pre", account=account_pre)
        tracker.log_position_snapshot(
            run_id=run_id,
            snapshot_stage="pre",
            positions=positions_pre,
        )

        if positions_pre.empty:
            tracker.update_run_finish(
                run_id,
                status="manual_liquidation_no_positions",
                reason="No open positions.",
            )
            print("No open positions found.")
            return 0

        if args.dry_run:
            stamp = _stamp_now_et()
            preview_orders = positions_pre.copy()
            preview_orders["liquidation_action"] = preview_orders["side"].map(
                lambda s: "buy_to_cover" if "short" in str(s).lower() else "sell"
            )
            _write_liquidation_csvs(
                logs_dir=cfg.logs_dir,
                stamp=stamp,
                positions_pre=positions_pre,
                positions_post=positions_pre,
                responses=preview_orders,
            )
            tracker.update_run_finish(
                run_id,
                status="manual_liquidation_dry_run",
                reason=json.dumps(
                    {
                        "position_count": int(len(positions_pre)),
                        "estimated_symbols": positions_pre["symbol"].tolist(),
                    },
                    ensure_ascii=True,
                ),
            )
            print(f"Dry-run complete. Positions to close: {len(positions_pre)}")
            print(f"Preview saved to: {cfg.logs_dir}")
            return 0

        if not args.yes:
            tracker.update_run_finish(
                run_id,
                status="manual_liquidation_aborted",
                reason="Refused to execute without --yes.",
            )
            print("Refusing to liquidate without --yes. Use --dry-run to preview.")
            return 2

        cancel_orders = not args.no_cancel_open_orders
        responses = broker.close_all_positions(cancel_orders=cancel_orders)

        timeout_seconds = max(5, int(args.timeout_seconds))
        poll_seconds = max(1, int(args.poll_seconds))
        deadline = time.time() + timeout_seconds
        positions_post = broker.list_positions()
        while not positions_post.empty and time.time() < deadline:
            time.sleep(poll_seconds)
            positions_post = broker.list_positions()

        account_post = broker.get_account_snapshot()
        tracker.log_account_snapshot(run_id=run_id, snapshot_stage="post", account=account_post)
        tracker.log_position_snapshot(
            run_id=run_id,
            snapshot_stage="post",
            positions=positions_post,
        )

        responses_frame = pd.DataFrame(responses)
        if responses_frame.empty:
            responses_frame = pd.DataFrame(
                columns=[
                    "symbol",
                    "status",
                    "order_id",
                    "order_side",
                    "order_qty",
                    "order_notional",
                ]
            )

        stamp = _stamp_now_et()
        _write_liquidation_csvs(
            logs_dir=cfg.logs_dir,
            stamp=stamp,
            positions_pre=positions_pre,
            positions_post=positions_post,
            responses=responses_frame,
        )

        status = "manual_liquidation_success" if positions_post.empty else "manual_liquidation_partial"
        reason_payload = {
            "cancel_open_orders": cancel_orders,
            "responses_count": int(len(responses_frame)),
            "positions_before": int(len(positions_pre)),
            "positions_after": int(len(positions_post)),
            "remaining_symbols": (
                positions_post["symbol"].astype(str).str.upper().tolist()
                if not positions_post.empty
                else []
            ),
            "csv_stamp": stamp,
        }
        tracker.update_run_finish(
            run_id,
            status=status,
            reason=json.dumps(reason_payload, ensure_ascii=True),
        )

        print(f"Liquidation submitted. run_id={run_id}")
        print(f"Status: {status}")
        print(f"Positions before: {len(positions_pre)}")
        print(f"Positions after: {len(positions_post)}")
        print(f"Logs: {cfg.logs_dir}")
        return 0 if positions_post.empty else 1

    except Exception as exc:
        tracker.update_run_finish(
            run_id,
            status="manual_liquidation_error",
            reason=str(exc),
        )
        raise


if __name__ == "__main__":
    raise SystemExit(main())
