from __future__ import annotations

from collections.abc import Iterable
from datetime import date
from math import isnan
from typing import Any

import pandas as pd

from classification_store import build_group_key, merge_proxy_classifications
from reference_data import group_pct_rank


ALPHA_EPS = 1e-6


def _clean_text(series: pd.Series, *, default: str | None = None) -> pd.Series:
    text = series.astype("string").str.strip()
    text = text.replace(
        {
            "": pd.NA,
            "<NA>": pd.NA,
            "NAN": pd.NA,
            "nan": pd.NA,
            "NONE": pd.NA,
            "None": pd.NA,
        }
    )
    if default is not None:
        text = text.fillna(default)
    return text


def normalize_bars(raw_bars: pd.DataFrame) -> pd.DataFrame:
    if raw_bars is None or raw_bars.empty:
        return pd.DataFrame(columns=["symbol", "trade_date", "o", "h", "l", "c", "v", "vw", "n"])

    bars = raw_bars.copy()
    bars["symbol"] = bars.get("symbol", pd.Series(dtype="object")).astype(str).str.strip().str.upper()
    if "trade_date" in bars.columns:
        bars["trade_date"] = pd.to_datetime(bars["trade_date"], errors="coerce").dt.date
    else:
        bars["trade_date"] = pd.to_datetime(bars.get("t", pd.Series(dtype="object")), utc=True, errors="coerce").dt.date

    for column in ["o", "h", "l", "c", "v", "vw", "n"]:
        bars[column] = pd.to_numeric(bars.get(column, pd.Series(dtype="float64")), errors="coerce")

    bars = bars.dropna(subset=["symbol", "trade_date", "o", "h", "l", "c", "v"])
    bars = bars[(bars["symbol"] != "") & (bars["o"] > 0) & (bars["h"] > 0) & (bars["l"] > 0) & (bars["c"] > 0)]
    bars["vw"] = bars["vw"].where(bars["vw"].astype(float) > 0, bars["c"])
    bars["n"] = bars["n"].where(bars["n"].notna(), 0.0)
    bars = bars.drop_duplicates(subset=["symbol", "trade_date"], keep="last")
    return bars[["symbol", "trade_date", "o", "h", "l", "c", "v", "vw", "n"]].sort_values(
        ["symbol", "trade_date"]
    ).reset_index(drop=True)


def _rolling_ts_rank(series: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        return pd.Series(1.0, index=series.index, dtype="float64")
    return series.rolling(window=window, min_periods=window).apply(
        lambda arr: float(pd.Series(arr).rank(pct=True).iloc[-1]),
        raw=False,
    )


def _rolling_pct_rank(series: pd.Series, window: int) -> pd.Series:
    if window <= 1:
        return pd.Series(1.0, index=series.index, dtype="float64")
    return series.rolling(window=window, min_periods=window).apply(
        lambda arr: float(pd.Series(arr).rank(pct=True).iloc[-1]),
        raw=False,
    )


def _prepare_base_panel(
    bars: pd.DataFrame,
    *,
    classifications: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    group_level: str = "auto",
) -> pd.DataFrame:
    data = normalize_bars(bars)
    if data.empty:
        return data

    data = data.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    grouped = data.groupby("symbol", sort=False)
    data["prev_close"] = grouped["c"].shift(1)
    data["ret_1d"] = grouped["c"].pct_change()
    data["ret_3d"] = grouped["c"].pct_change(3)
    data["ret_5d"] = grouped["c"].pct_change(5)
    data["adv20"] = grouped["v"].transform(lambda s: s.rolling(window=20, min_periods=5).mean())
    data["vol_ratio"] = data["v"].astype(float).div(data["adv20"].astype(float) + ALPHA_EPS)
    data["intraday_range"] = (data["h"].astype(float) - data["l"].astype(float)).abs()
    data["intraday_body"] = data["c"].astype(float).sub(data["o"].astype(float))
    data["intraday_fade_raw"] = data["intraday_body"].div(data["intraday_range"] + ALPHA_EPS)
    data["gap_raw"] = data["o"].astype(float).sub(data["prev_close"].astype(float)).div(
        data["prev_close"].astype(float) + ALPHA_EPS
    )
    data["vwap_gap_raw"] = data["c"].astype(float).sub(data["vw"].astype(float)).div(
        data["vw"].astype(float).abs() + ALPHA_EPS
    )
    data["close_to_breakout"] = grouped["c"].transform(
        lambda s: (s - s.rolling(window=20, min_periods=20).min()).div(
            s.rolling(window=20, min_periods=20).max()
            - s.rolling(window=20, min_periods=20).min()
            + ALPHA_EPS
        )
    )
    data["ret_mean_20"] = grouped["ret_1d"].transform(
        lambda s: s.rolling(window=20, min_periods=20).mean()
    )
    data["ret_std_20"] = grouped["ret_1d"].transform(
        lambda s: s.rolling(window=20, min_periods=20).std(ddof=0)
    )

    data = merge_proxy_classifications(
        data,
        classifications=classifications,
        sector_map=sector_map,
    )
    data["sector"] = _clean_text(data["sector"], default="ALL")
    data["industry"] = _clean_text(data["industry"])
    data["group_key"] = build_group_key(data, group_level=group_level)
    return data.reset_index(drop=True)


def _apply_formula(panel: pd.DataFrame, alpha_name: str, params: dict[str, Any]) -> pd.Series:
    grouped = panel.groupby("symbol", sort=False)
    name = str(alpha_name).strip().lower()

    if name == "rev_close_1d":
        return -panel["ret_1d"].astype(float)

    if name == "rev_close_3d":
        lookback = int(params.get("lookback", 3))
        return -grouped["c"].pct_change(lookback).astype(float)

    if name == "intraday_fade":
        return -panel["intraday_fade_raw"].astype(float)

    if name == "gap_fade":
        return -panel["gap_raw"].astype(float)

    if name == "vwap_gap_revert":
        return -panel["vwap_gap_raw"].astype(float)

    if name == "vwap_extreme_revert":
        window = int(params.get("window", 3))
        return -grouped["vwap_gap_raw"].transform(lambda s: _rolling_ts_rank(s.astype(float), window))

    if name == "pv_corr_contra":
        corr_window = int(params.get("corr_window", 5))
        rank_window = int(params.get("rank_window", 3))
        close_rank = grouped["c"].transform(lambda s: _rolling_pct_rank(s.astype(float), corr_window))
        volume_rank = grouped["v"].transform(lambda s: _rolling_pct_rank(s.astype(float), corr_window))
        corr = (
            pd.DataFrame({"symbol": panel["symbol"], "close_rank": close_rank, "volume_rank": volume_rank})
            .groupby("symbol", sort=False)
            .apply(
                lambda frame: frame["close_rank"].rolling(window=corr_window, min_periods=corr_window).corr(
                    frame["volume_rank"]
                )
            )
            .reset_index(level=0, drop=True)
        )
        return -corr.groupby(panel["symbol"], sort=False).transform(
            lambda s: _rolling_ts_rank(s.astype(float), rank_window)
        )

    if name == "volshock_reversal":
        return -(panel["ret_1d"].astype(float) * panel["vol_ratio"].astype(float))

    if name == "adv_participation_revert":
        lookback = int(params.get("lookback", 3))
        ret_n = grouped["c"].pct_change(lookback)
        return -(ret_n.astype(float) * panel["vol_ratio"].astype(float))

    if name == "smooth_momentum":
        window = int(params.get("window", 20))
        mean_ret = grouped["ret_1d"].transform(lambda s: s.rolling(window=window, min_periods=window).mean())
        std_ret = grouped["ret_1d"].transform(lambda s: s.rolling(window=window, min_periods=window).std(ddof=0))
        return mean_ret.astype(float).div(std_ret.astype(float) + ALPHA_EPS)

    if name == "skip_month_momentum":
        lookback = int(params.get("lookback", 126))
        skip = int(params.get("skip", 21))
        prior = grouped["c"].shift(skip)
        base = grouped["c"].shift(lookback)
        return prior.astype(float).div(base.astype(float) + ALPHA_EPS) - 1.0

    if name == "high_52w_proximity":
        window = int(params.get("window", 252))
        trailing_high = grouped["c"].transform(lambda s: s.rolling(window=window, min_periods=window).max())
        return panel["c"].astype(float).div(trailing_high.astype(float) + ALPHA_EPS) - 1.0

    if name == "low_volatility_defensive":
        window = int(params.get("window", 63))
        volatility = grouped["ret_1d"].transform(
            lambda s: s.rolling(window=window, min_periods=window).std(ddof=0)
        )
        return -volatility.astype(float)

    if name == "breakout_quality":
        window = int(params.get("window", 20))
        breakout = grouped["c"].transform(
            lambda s: (s - s.rolling(window=window, min_periods=window).min()).div(
                s.rolling(window=window, min_periods=window).max()
                - s.rolling(window=window, min_periods=window).min()
                + ALPHA_EPS
            )
        )
        volatility = grouped["ret_1d"].transform(
            lambda s: s.rolling(window=window, min_periods=window).std(ddof=0)
        )
        return breakout.astype(float) - volatility.astype(float)

    if name == "momentum_with_volume_confirm":
        window = int(params.get("window", 10))
        mean_ret = grouped["ret_1d"].transform(lambda s: s.rolling(window=window, min_periods=window).mean())
        return mean_ret.astype(float) * panel["vol_ratio"].astype(float)

    raise KeyError(f"Unsupported alpha template: {alpha_name}")


def compute_alpha_panel(
    alpha_name: str,
    bars: pd.DataFrame,
    *,
    classifications: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    group_level: str = "auto",
    params: dict[str, Any] | None = None,
) -> pd.DataFrame:
    params = dict(params or {})
    name = str(alpha_name).strip().lower()
    if name in {"profit_asset_gate_proxy_v1", "profit_asset_gate_proxy_v2"}:
        from signal_generator import compute_profit_asset_gate_proxy_panel

        gate_params = {
            "profit_window": int(params.get("profit_window", 63)),
            "asset_window": int(params.get("asset_window", 63)),
            "mom_window": int(params.get("mom_window", 5)),
        }
        return compute_profit_asset_gate_proxy_panel(
            bars,
            classifications=classifications,
            sector_map=sector_map,
            group_level=group_level,
            **gate_params,
        )

    panel = _prepare_base_panel(
        bars,
        classifications=classifications,
        sector_map=sector_map,
        group_level=group_level,
    )
    if panel.empty:
        return pd.DataFrame()
    panel["alpha_raw"] = _apply_formula(panel, name, params)
    return panel.reset_index(drop=True)


def _truncate_score_series(series: pd.Series, score_truncation: float | None) -> pd.Series:
    scores = pd.to_numeric(series, errors="coerce").fillna(0.0).astype(float)
    if score_truncation is None:
        return scores
    limit = float(score_truncation)
    if limit <= 0:
        return scores
    abs_sum = float(scores.abs().sum())
    if abs_sum <= 0:
        return scores
    contribution = scores / abs_sum
    return contribution.clip(lower=-limit, upper=limit)


def score_alpha_frame(
    alpha_name: str,
    latest: pd.DataFrame,
    *,
    min_scored_symbols: int = 50,
    score_truncation: float | None = None,
    params: dict[str, Any] | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    params = dict(params or {})
    name = str(alpha_name).strip().lower()
    if latest.empty:
        raise ValueError("No latest-date rows available to score.")

    if name in {"profit_asset_gate_proxy_v1", "profit_asset_gate_proxy_v2"}:
        from signal_generator import score_profit_asset_gate_proxy_frame

        asset_gate_threshold = float(params.get("asset_gate_threshold", 0.5))
        scores, diagnostics = score_profit_asset_gate_proxy_frame(
            latest.copy(),
            min_scored_symbols=min_scored_symbols,
            asset_gate_threshold=asset_gate_threshold,
        )
        scores["base_score"] = pd.to_numeric(scores["score"], errors="coerce").fillna(0.0)
        scores["score"] = _truncate_score_series(scores["base_score"], score_truncation)
        diagnostics = diagnostics.merge(
            scores[["symbol", "base_score", "score"]],
            on="symbol",
            how="left",
        )
        return scores[["symbol", "score", "sector", "base_score"]], diagnostics

    working = latest.copy()
    missing_reason = pd.Series("", index=working.index, dtype="object")
    missing_reason.loc[working["alpha_raw"].isna()] = "insufficient_history"
    working["missing_reason"] = missing_reason
    working["alpha_rank"] = group_pct_rank(working, "alpha_raw", "group_key")
    working["base_score"] = working["alpha_rank"].astype(float) - 0.5
    valid_mask = working["missing_reason"].eq("")
    valid_count = int(valid_mask.sum())
    if valid_count < int(min_scored_symbols):
        raise ValueError(
            f"Coverage too low for {alpha_name}: {valid_count} valid symbols < required {int(min_scored_symbols)}."
        )
    working["score"] = 0.0
    working.loc[valid_mask, "score"] = _truncate_score_series(
        working.loc[valid_mask, "base_score"],
        score_truncation,
    )
    scores = working[["symbol", "score", "sector", "base_score"]].copy()
    diagnostics = working[
        [
            "symbol",
            "canonical_symbol",
            "sector",
            "industry",
            "group_key",
            "alpha_raw",
            "alpha_rank",
            "base_score",
            "score",
            "missing_reason",
        ]
    ].copy()
    return scores, diagnostics


def apply_signal_decay(score_panel: pd.DataFrame, *, decay_days: int) -> pd.DataFrame:
    out = score_panel.copy()
    if out.empty or int(decay_days) <= 1:
        return out

    window = int(decay_days)
    weights = pd.Series(range(1, window + 1), dtype="float64")

    def decay_series(series: pd.Series) -> pd.Series:
        return series.rolling(window=window, min_periods=1).apply(
            lambda arr: float(
                pd.Series(arr, dtype="float64").mul(weights.iloc[-len(arr) :].to_numpy()).sum()
                / weights.iloc[-len(arr) :].sum()
            ),
            raw=False,
        )

    out = out.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    out["score"] = out.groupby("symbol", sort=False)["score"].transform(decay_series)
    return out


def compute_alpha_score_panel(
    alpha_name: str,
    bars: pd.DataFrame,
    *,
    classifications: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    group_level: str = "auto",
    params: dict[str, Any] | None = None,
    score_truncation: float | None = None,
    signal_decay: int = 0,
    signal_dates: Iterable[date] | None = None,
    min_scored_symbols: int = 50,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = compute_alpha_panel(
        alpha_name,
        bars,
        classifications=classifications,
        sector_map=sector_map,
        group_level=group_level,
        params=params,
    )
    if panel.empty:
        return pd.DataFrame(), pd.DataFrame()

    allowed_dates = {dt for dt in signal_dates or []}
    if allowed_dates:
        panel = panel[panel["trade_date"].isin(allowed_dates)].copy()
    if panel.empty:
        return pd.DataFrame(), pd.DataFrame()

    score_rows: list[pd.DataFrame] = []
    diag_rows: list[pd.DataFrame] = []
    for trade_date_value, latest in panel.groupby("trade_date", sort=True):
        try:
            scores, diagnostics = score_alpha_frame(
                alpha_name,
                latest.copy(),
                min_scored_symbols=min_scored_symbols,
                score_truncation=score_truncation,
                params=params,
            )
        except ValueError as exc:
            if "Coverage too low" in str(exc):
                continue
            raise
        scores = latest[["symbol", "trade_date", "sector"]].merge(
            scores[["symbol", "score", "base_score"]],
            on="symbol",
            how="left",
        )
        diagnostics["trade_date"] = trade_date_value
        score_rows.append(scores)
        diag_rows.append(diagnostics)

    score_panel = pd.concat(score_rows, ignore_index=True) if score_rows else pd.DataFrame()
    diagnostics_panel = pd.concat(diag_rows, ignore_index=True) if diag_rows else pd.DataFrame()
    if score_panel.empty:
        return score_panel, diagnostics_panel

    score_panel = apply_signal_decay(score_panel, decay_days=signal_decay)
    diagnostics_panel = diagnostics_panel.merge(
        score_panel[["symbol", "trade_date", "score"]],
        on=["symbol", "trade_date"],
        how="left",
        suffixes=("", "_post_decay"),
    )
    return score_panel.reset_index(drop=True), diagnostics_panel.reset_index(drop=True)


def combine_strategy_score_panels(
    member_panels: list[tuple[dict[str, Any], pd.DataFrame]],
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    for member, panel in member_panels:
        if panel.empty:
            continue
        weight = float(member.get("weight", 0.0))
        if weight == 0:
            continue
        member_frame = panel[["symbol", "trade_date", "sector", "score"]].copy()
        member_frame["score"] = member_frame["score"].astype(float) * weight
        frames.append(member_frame)

    if not frames:
        return pd.DataFrame(columns=["symbol", "trade_date", "sector", "score"])

    combined = pd.concat(frames, ignore_index=True)
    combined = (
        combined.groupby(["symbol", "trade_date"], as_index=False)
        .agg(score=("score", "sum"), sector=("sector", "first"))
        .sort_values(["trade_date", "score", "symbol"], ascending=[True, False, True])
        .reset_index(drop=True)
    )
    return combined
