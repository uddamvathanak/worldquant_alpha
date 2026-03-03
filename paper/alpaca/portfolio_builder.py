from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from signal_loader import select_long_short_candidates


@dataclass(slots=True)
class BuildResult:
    targets: pd.DataFrame
    stats: dict[str, Any]
    filtered_short_symbols: list[str]


def _pick_sector_matched_books(
    longs: pd.DataFrame,
    shorts: pd.DataFrame,
) -> tuple[pd.DataFrame, pd.DataFrame, dict[str, int]]:
    long_counts = longs["sector"].value_counts().to_dict()
    short_counts = shorts["sector"].value_counts().to_dict()
    common = sorted(set(long_counts) & set(short_counts))
    matched_counts = {sector: min(long_counts[sector], short_counts[sector]) for sector in common}
    matched_counts = {k: v for k, v in matched_counts.items() if v > 0}

    if not matched_counts:
        return pd.DataFrame(), pd.DataFrame(), {}

    long_parts: list[pd.DataFrame] = []
    short_parts: list[pd.DataFrame] = []
    for sector, matched_n in matched_counts.items():
        long_sector = (
            longs[longs["sector"] == sector]
            .sort_values("score", ascending=False)
            .head(matched_n)
            .copy()
        )
        short_sector = (
            shorts[shorts["sector"] == sector]
            .sort_values("score", ascending=True)
            .head(matched_n)
            .copy()
        )
        long_parts.append(long_sector)
        short_parts.append(short_sector)
    long_book = pd.concat(long_parts, ignore_index=True)
    short_book = pd.concat(short_parts, ignore_index=True)
    return long_book, short_book, matched_counts


def _assign_uniform_sector_weights(
    long_book: pd.DataFrame,
    short_book: pd.DataFrame,
    *,
    long_gross: float,
    short_gross: float,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if long_book.empty or short_book.empty:
        return long_book, short_book

    long_book = long_book.copy()
    short_book = short_book.copy()
    long_weight = long_gross / len(long_book)
    short_weight = -short_gross / len(short_book)
    long_book["target_weight"] = long_weight
    short_book["target_weight"] = short_weight
    return long_book, short_book


def build_sector_neutral_targets(
    signals: pd.DataFrame,
    *,
    equity: float,
    top_n: int,
    gross_exposure: float,
    shortable_map: dict[str, bool] | None = None,
) -> BuildResult:
    longs, shorts = select_long_short_candidates(signals, top_n=top_n)

    filtered_short_symbols: list[str] = []
    if shortable_map is not None:
        is_shortable = shorts["symbol"].map(lambda s: bool(shortable_map.get(s, False)))
        filtered_short_symbols = shorts.loc[~is_shortable, "symbol"].tolist()
        shorts = shorts[is_shortable].copy()

    long_gross = gross_exposure / 2.0
    short_gross = gross_exposure / 2.0

    long_book, short_book, matched_counts = _pick_sector_matched_books(longs, shorts)

    fallback_used = False
    if long_book.empty or short_book.empty:
        fallback_used = True
        balanced_n = min(len(longs), len(shorts))
        if balanced_n <= 0:
            targets = pd.DataFrame(
                columns=[
                    "symbol",
                    "side",
                    "sector",
                    "score",
                    "target_weight",
                    "target_notional",
                ]
            )
            stats = {
                "long_count": 0,
                "short_count": 0,
                "matched_sector_count": 0,
                "long_gross_target": long_gross,
                "short_gross_target": short_gross,
                "net_target": 0.0,
                "fallback_used": True,
            }
            return BuildResult(
                targets=targets,
                stats=stats,
                filtered_short_symbols=filtered_short_symbols,
            )
        long_book = longs.sort_values("score", ascending=False).head(balanced_n).copy()
        short_book = shorts.sort_values("score", ascending=True).head(balanced_n).copy()
        matched_counts = {}

    long_book, short_book = _assign_uniform_sector_weights(
        long_book,
        short_book,
        long_gross=long_gross,
        short_gross=short_gross,
    )

    long_book["side"] = "long"
    short_book["side"] = "short"
    targets = pd.concat([long_book, short_book], ignore_index=True)
    targets["target_notional"] = targets["target_weight"] * float(equity)
    targets = targets[
        ["symbol", "side", "sector", "score", "target_weight", "target_notional"]
    ].sort_values(["side", "symbol"], ascending=[True, True])
    targets = targets.reset_index(drop=True)

    stats = {
        "long_count": int((targets["side"] == "long").sum()),
        "short_count": int((targets["side"] == "short").sum()),
        "matched_sector_count": len(matched_counts),
        "matched_sector_map": matched_counts,
        "long_gross_target": float(targets.loc[targets["side"] == "long", "target_weight"].sum()),
        "short_gross_target": float(
            -targets.loc[targets["side"] == "short", "target_weight"].sum()
        ),
        "net_target": float(targets["target_weight"].sum()),
        "fallback_used": fallback_used,
    }
    return BuildResult(
        targets=targets,
        stats=stats,
        filtered_short_symbols=filtered_short_symbols,
    )


def drop_and_rescale_rejected_shorts(
    targets: pd.DataFrame,
    rejected_symbols: list[str],
    *,
    short_gross_target: float,
) -> pd.DataFrame:
    if targets.empty or not rejected_symbols:
        return targets.copy()

    out = targets[~targets["symbol"].isin(rejected_symbols)].copy()
    shorts = out[out["side"] == "short"].copy()
    if shorts.empty:
        return out

    current_abs = float(shorts["target_weight"].abs().sum())
    if current_abs <= 0:
        return out

    scale = short_gross_target / current_abs
    out.loc[out["side"] == "short", "target_weight"] *= scale
    out.loc[out["side"] == "short", "target_notional"] *= scale
    return out.reset_index(drop=True)


def drop_rejected_shorts_and_reneutralize(
    targets: pd.DataFrame,
    rejected_symbols: list[str],
    *,
    short_gross_target: float,
) -> pd.DataFrame:
    if targets.empty or not rejected_symbols:
        return targets.copy()

    out = targets.copy()
    reject_set = {str(symbol).strip().upper() for symbol in rejected_symbols}
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out = out[~((out["side"] == "short") & (out["symbol"].isin(reject_set)))].copy()

    shorts = out[out["side"] == "short"].copy()
    longs = out[out["side"] == "long"].copy()

    if shorts.empty:
        if not longs.empty:
            out.loc[out["side"] == "long", "target_weight"] = 0.0
            out.loc[out["side"] == "long", "target_notional"] = 0.0
        return out.reset_index(drop=True)

    current_short_abs = float(shorts["target_weight"].abs().sum())
    if current_short_abs <= 0:
        if not longs.empty:
            out.loc[out["side"] == "long", "target_weight"] = 0.0
            out.loc[out["side"] == "long", "target_notional"] = 0.0
        return out.reset_index(drop=True)

    short_scale = float(short_gross_target / current_short_abs)
    out.loc[out["side"] == "short", "target_weight"] *= short_scale
    out.loc[out["side"] == "short", "target_notional"] *= short_scale

    achieved_short_abs = float(out.loc[out["side"] == "short", "target_weight"].abs().sum())
    long_gross = float(out.loc[out["side"] == "long", "target_weight"].sum())
    if long_gross > 0:
        long_scale = float(achieved_short_abs / long_gross)
        out.loc[out["side"] == "long", "target_weight"] *= long_scale
        out.loc[out["side"] == "long", "target_notional"] *= long_scale

    return out.reset_index(drop=True)


def portfolio_exposure(targets: pd.DataFrame) -> dict[str, float]:
    long_gross = float(targets.loc[targets["side"] == "long", "target_weight"].sum())
    short_gross = float(-targets.loc[targets["side"] == "short", "target_weight"].sum())
    net = float(targets["target_weight"].sum())
    return {
        "long_gross": long_gross,
        "short_gross": short_gross,
        "net": net,
    }
