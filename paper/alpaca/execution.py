from __future__ import annotations

from datetime import datetime
from typing import Iterable
import uuid

import pandas as pd

from config import ET, UTC


ACCEPTED_ORDER_STATUSES = {
    "new",
    "accepted",
    "accepted_for_bidding",
    "partially_filled",
    "filled",
    "pending_new",
    "held",
    "done_for_day",
    "calculated",
    "dry_run",
}


def build_order_plan(
    targets: pd.DataFrame,
    positions: pd.DataFrame,
    *,
    min_order_notional: float,
) -> pd.DataFrame:
    if targets.empty:
        return pd.DataFrame(
            columns=[
                "symbol",
                "target_side",
                "sector",
                "score",
                "target_weight",
                "target_notional",
                "current_notional",
                "delta_notional",
                "order_side",
                "order_notional",
            ]
        )

    current_map: dict[str, float] = {}
    if not positions.empty:
        for _, row in positions.iterrows():
            current_map[str(row["symbol"]).strip().upper()] = float(
                row.get("signed_market_value", row.get("market_value", 0.0))
            )

    plan = targets.copy()
    plan["current_notional"] = plan["symbol"].map(lambda s: current_map.get(s, 0.0))
    plan["delta_notional"] = plan["target_notional"] - plan["current_notional"]
    plan["order_side"] = plan["delta_notional"].map(lambda v: "buy" if v > 0 else "sell")
    plan["order_notional"] = plan["delta_notional"].abs()
    plan = plan[plan["order_notional"] >= float(min_order_notional)].copy()
    plan = plan.sort_values("order_notional", ascending=False).reset_index(drop=True)
    plan = plan.rename(columns={"side": "target_side"})
    cols = [
        "symbol",
        "target_side",
        "sector",
        "score",
        "target_weight",
        "target_notional",
        "current_notional",
        "delta_notional",
        "order_side",
        "order_notional",
    ]
    return plan[cols]


def execute_order_plan(
    broker: object,
    order_plan: pd.DataFrame,
    *,
    run_id: str,
    pass_num: int,
    dry_run: bool,
) -> pd.DataFrame:
    if order_plan.empty:
        return pd.DataFrame(
            columns=[
                "run_id",
                "event_ts_utc",
                "event_ts_et",
                "symbol",
                "target_side",
                "order_side",
                "order_notional",
                "target_weight",
                "target_notional",
                "current_notional",
                "delta_notional",
                "order_id",
                "status",
                "error",
                "pass_num",
            ]
        )

    records: list[dict[str, object]] = []
    for _, row in order_plan.iterrows():
        now_utc = datetime.now(UTC)
        now_et = now_utc.astimezone(ET)
        symbol = str(row["symbol"])
        order_side = str(row["order_side"])
        order_notional = float(row["order_notional"])
        order_id = ""
        status = ""
        error = ""
        if dry_run:
            status = "dry_run"
        else:
            try:
                client_order_id = f"{run_id}-p{pass_num}-{uuid.uuid4().hex[:8]}"
                result = broker.submit_market_notional_order(
                    symbol=symbol,
                    side=order_side,
                    notional=order_notional,
                    client_order_id=client_order_id,
                )
                order_id = str(result.get("order_id", ""))
                status = str(result.get("status", "submitted")).strip().lower()
            except Exception as exc:  # pragma: no cover - integration path
                status = "rejected"
                error = str(exc)

        records.append(
            {
                "run_id": run_id,
                "event_ts_utc": now_utc.isoformat(),
                "event_ts_et": now_et.isoformat(),
                "symbol": symbol,
                "target_side": str(row["target_side"]),
                "order_side": order_side,
                "order_notional": order_notional,
                "target_weight": float(row["target_weight"]),
                "target_notional": float(row["target_notional"]),
                "current_notional": float(row["current_notional"]),
                "delta_notional": float(row["delta_notional"]),
                "order_id": order_id,
                "status": status,
                "error": error,
                "pass_num": int(pass_num),
            }
        )
    return pd.DataFrame(records)


def extract_rejected_short_symbols(events: pd.DataFrame) -> list[str]:
    if events.empty:
        return []
    rejected = events[
        (events["target_side"] == "short")
        & (
            events["status"].astype(str).str.contains("reject|error", case=False, regex=True)
        )
    ]
    symbols = rejected["symbol"].astype(str).str.upper().unique().tolist()
    return sorted(symbols)


def estimate_traded_notional(events: pd.DataFrame) -> float:
    if events.empty:
        return 0.0
    statuses = events["status"].astype(str).str.lower()
    accepted_mask = statuses.isin(ACCEPTED_ORDER_STATUSES)
    return float(events.loc[accepted_mask, "order_notional"].sum())


def estimate_turnover(traded_notional: float, prev_equity: float | None) -> float:
    if not prev_equity or prev_equity <= 0:
        return 0.0
    return float(traded_notional / prev_equity)


def successful_symbols(events: pd.DataFrame) -> list[str]:
    if events.empty:
        return []
    statuses = events["status"].astype(str).str.lower()
    mask = statuses.isin(ACCEPTED_ORDER_STATUSES)
    return sorted(events.loc[mask, "symbol"].astype(str).str.upper().unique().tolist())


def failed_symbols(events: pd.DataFrame) -> list[str]:
    if events.empty:
        return []
    statuses = events["status"].astype(str).str.lower()
    mask = ~statuses.isin(ACCEPTED_ORDER_STATUSES)
    return sorted(events.loc[mask, "symbol"].astype(str).str.upper().unique().tolist())


def merge_event_frames(frames: Iterable[pd.DataFrame]) -> pd.DataFrame:
    filtered = [frame for frame in frames if frame is not None and not frame.empty]
    if not filtered:
        return pd.DataFrame()
    return pd.concat(filtered, ignore_index=True)

