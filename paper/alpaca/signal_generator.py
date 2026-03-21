from __future__ import annotations

import argparse
from datetime import date, timedelta
from pathlib import Path

import pandas as pd

from alpha_registry import (
    MODEL_RESEARCH_SELECTED,
    get_alpha_definition,
    load_strategy_spec,
    registry_model_names,
    selected_strategy_path,
)
from alpha_templates import (
    combine_strategy_score_panels,
    compute_alpha_score_panel,
)
from broker_alpaca import AlpacaBroker
from classification_store import (
    ClassificationStoreError,
    build_group_key,
    load_classifications_snapshot,
    merge_proxy_classifications,
    resolve_classification_snapshot_path,
)
from config import load_config, parse_trade_date
from reference_data import (
    ReferenceDataError,
    group_pct_rank,
    load_classifications_asof,
    load_fundamentals_asof,
)
from signal_loader import signal_path_for_date


MODEL_FAILED_MOVE_VWAP = "failed_move_vwap"
MODEL_PROFIT_ASSET_GATE = "profit_asset_gate"
MODEL_PROFIT_ASSET_GATE_PROXY = "profit_asset_gate_proxy"
BASE_MODEL_CHOICES = [
    MODEL_FAILED_MOVE_VWAP,
    MODEL_PROFIT_ASSET_GATE,
    MODEL_PROFIT_ASSET_GATE_PROXY,
    MODEL_RESEARCH_SELECTED,
]


def list_signal_models() -> list[str]:
    return sorted(set(BASE_MODEL_CHOICES + registry_model_names()))


MODEL_CHOICES = list_signal_models()


class SignalGenerationError(RuntimeError):
    pass


def _read_symbol_list_from_csv(path: Path) -> list[str]:
    frame = pd.read_csv(path)
    if frame.empty:
        return []

    lower = {str(col).strip().lower(): col for col in frame.columns}
    if "symbol" in lower:
        series = frame[lower["symbol"]]
    else:
        series = frame.iloc[:, 0]

    symbols = (
        series.astype(str)
        .str.strip()
        .str.upper()
        .replace("", pd.NA)
        .dropna()
        .tolist()
    )
    return sorted(set(symbols))


def _latest_signal_file(signals_dir: Path) -> Path | None:
    if not signals_dir.exists():
        return None
    files = [
        p
        for p in signals_dir.glob("*.csv")
        if p.name.lower() != ".gitkeep" and p.is_file()
    ]
    if not files:
        return None
    return sorted(files, key=lambda p: p.stat().st_mtime, reverse=True)[0]


def load_universe_symbols(
    *,
    universe_file: Path,
    signals_dir: Path,
) -> list[str]:
    if universe_file.exists():
        symbols = _read_symbol_list_from_csv(universe_file)
        if symbols:
            return symbols

    latest_signal = _latest_signal_file(signals_dir)
    if latest_signal is not None:
        frame = pd.read_csv(latest_signal)
        lower = {str(col).strip().lower(): col for col in frame.columns}
        if "symbol" in lower:
            symbols = (
                frame[lower["symbol"]]
                .astype(str)
                .str.strip()
                .str.upper()
                .replace("", pd.NA)
                .dropna()
                .tolist()
            )
            symbols = sorted(set(symbols))
            if symbols:
                return symbols

    raise SignalGenerationError(
        "No universe symbols available. Provide paper/alpaca/private/universe.csv "
        "with a 'symbol' column or keep at least one prior signals CSV."
    )


def load_sector_map(path: Path) -> dict[str, str]:
    if not path.exists():
        return {}

    frame = pd.read_csv(path)
    if frame.empty:
        return {}

    lower = {str(col).strip().lower(): col for col in frame.columns}
    if "symbol" not in lower or "sector" not in lower:
        raise SignalGenerationError(
            f"Sector map {path} must include columns: symbol, sector"
        )

    out: dict[str, str] = {}
    for _, row in frame.iterrows():
        symbol = str(row[lower["symbol"]]).strip().upper()
        sector = str(row[lower["sector"]]).strip()
        if symbol and sector:
            out[symbol] = sector
    return out


def load_cached_classifications(
    reference_dir: Path,
    *,
    snapshot_date: date | None = None,
    required: bool = False,
) -> tuple[pd.DataFrame, Path | None]:
    try:
        path = resolve_classification_snapshot_path(
            reference_dir,
            snapshot_date=snapshot_date,
        )
        frame = load_classifications_snapshot(
            reference_dir,
            snapshot_date=snapshot_date,
        )
        return frame, path
    except ClassificationStoreError as exc:
        if required:
            raise SignalGenerationError(
                "No cached classification snapshot found for profit_asset_gate_proxy. "
                "Run classification_sync.py first."
            ) from exc
        return pd.DataFrame(), None


def _normalize_bars(raw_bars: pd.DataFrame) -> pd.DataFrame:
    if raw_bars.empty:
        raise SignalGenerationError("No daily bars returned from Alpaca data API.")

    bars = raw_bars.copy()
    bars["symbol"] = bars["symbol"].astype(str).str.strip().str.upper()
    if "trade_date" in bars.columns:
        bars["trade_date"] = pd.to_datetime(
            bars["trade_date"],
            errors="coerce",
        ).dt.date
    else:
        bars["trade_date"] = pd.to_datetime(bars["t"], utc=True, errors="coerce").dt.date

    for col in ["o", "h", "l", "c", "v", "vw"]:
        bars[col] = pd.to_numeric(bars[col], errors="coerce")

    bars = bars.dropna(subset=["symbol", "trade_date", "o", "h", "l", "c", "v"])
    bars = bars[bars["o"] > 0]
    bars = bars[bars["c"] > 0]
    bars = bars[bars["h"] > 0]
    bars = bars[bars["l"] > 0]
    bars["vw"] = bars["vw"].where(bars["vw"] > 0, bars["c"])
    bars = bars.sort_values(["trade_date", "symbol"]).reset_index(drop=True)

    if bars.empty:
        raise SignalGenerationError("Bars are empty after cleaning.")
    return bars


def _latest_completed_trade_date(bars: pd.DataFrame) -> date:
    if bars.empty or "trade_date" not in bars.columns:
        raise SignalGenerationError("Unable to determine latest trade_date from bars.")
    latest = bars["trade_date"].max()
    if pd.isna(latest):
        raise SignalGenerationError("Unable to determine latest trade_date from bars.")
    return latest


def compute_failed_move_vwap_scores(
    bars: pd.DataFrame,
    *,
    smoothing: int = 2,
) -> pd.DataFrame:
    if smoothing <= 0:
        raise ValueError("smoothing must be positive.")

    data = _normalize_bars(bars)
    data["id"] = (data["c"] - data["o"]) / data["o"]
    data["ext"] = (data["c"] - data["vw"]) / data["vw"]

    data["id_rank"] = data.groupby("trade_date")["id"].rank(method="average", pct=True)
    data["ext_rank"] = data.groupby("trade_date")["ext"].rank(method="average", pct=True)
    data["combo"] = -data["id_rank"] * data["ext_rank"]

    data = data.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    data["score"] = (
        data.groupby("symbol")["combo"]
        .transform(lambda s: s.rolling(window=smoothing, min_periods=smoothing).mean())
    )

    latest_date = _latest_completed_trade_date(data)
    out = data[data["trade_date"] == latest_date][["symbol", "score"]].copy()
    out = out.dropna(subset=["score"])
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    if out.empty:
        raise SignalGenerationError(
            "No scores produced at the latest trade_date. Increase lookback window."
        )
    return out


def _clean_text(series: pd.Series, default: str | None = None) -> pd.Series:
    text = series.astype(str).str.strip()
    text = text.replace(
        {
            "": pd.NA,
            "NAN": pd.NA,
            "nan": pd.NA,
            "None": pd.NA,
            "none": pd.NA,
            "<NA>": pd.NA,
        }
    )
    if default is not None:
        text = text.fillna(default)
    return text


def compute_profit_asset_gate_scores(
    symbols: list[str],
    bars: pd.DataFrame,
    *,
    fundamentals: pd.DataFrame,
    classifications: pd.DataFrame,
    asof_date: date,
    min_scored_symbols: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    data = _normalize_bars(bars)
    data = data[data["trade_date"] <= asof_date].copy()
    if data.empty:
        raise SignalGenerationError("No bars available on or before the requested as-of date.")

    latest_trade_date = _latest_completed_trade_date(data)
    if latest_trade_date != asof_date:
        raise SignalGenerationError(
            f"Latest completed bar date {latest_trade_date.isoformat()} "
            f"does not match requested data as-of date {asof_date.isoformat()}."
        )

    data = data.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    data["returns"] = data.groupby("symbol")["c"].pct_change()
    data["mom_raw"] = (
        data.groupby("symbol")["returns"]
        .transform(lambda s: s.rolling(window=5, min_periods=5).mean())
    )

    base = pd.DataFrame({"symbol": sorted(set(symbols))})
    latest = data[data["trade_date"] == asof_date][["symbol", "mom_raw"]].copy()
    latest["has_latest_bar"] = True

    fundamentals_frame = fundamentals.rename(
        columns={"effective_date": "fundamentals_effective_date"}
    ).copy()
    classifications_frame = classifications.rename(
        columns={"effective_date": "classifications_effective_date"}
    ).copy()

    merged = (
        base.merge(latest, on="symbol", how="left")
        .merge(fundamentals_frame, on="symbol", how="left")
        .merge(classifications_frame, on="symbol", how="left")
    )

    merged["has_latest_bar"] = merged["has_latest_bar"].fillna(False).astype(bool)
    merged["sector"] = _clean_text(merged["sector"], default="UNMAPPED")
    merged["industry"] = _clean_text(merged["industry"])
    for col in ["mom_raw", "fnd2_ebitdm", "fnd2_ebitfr", "fn_assets_fair_val_a"]:
        merged[col] = pd.to_numeric(merged[col], errors="coerce")

    missing_reason = pd.Series("", index=merged.index, dtype="object")
    no_latest_bar = ~merged["has_latest_bar"]
    missing_reason.loc[no_latest_bar] = "missing_latest_bar"

    insufficient_history = missing_reason.eq("") & merged["mom_raw"].isna()
    missing_reason.loc[insufficient_history] = "insufficient_return_history"

    missing_fundamental_row = (
        missing_reason.eq("") & merged["fundamentals_effective_date"].isna()
    )
    missing_reason.loc[missing_fundamental_row] = "missing_fundamental"

    stale_fundamental = (
        missing_reason.eq("")
        & merged["is_stale"].fillna(True).astype(bool)
    )
    missing_reason.loc[stale_fundamental] = "stale_fundamental"

    missing_fundamental_value = (
        missing_reason.eq("")
        & merged[["fnd2_ebitdm", "fnd2_ebitfr", "fn_assets_fair_val_a"]].isna().any(axis=1)
    )
    missing_reason.loc[missing_fundamental_value] = "missing_fundamental"

    missing_industry = missing_reason.eq("") & merged["industry"].isna()
    missing_reason.loc[missing_industry] = "missing_industry"

    merged["profit_leg_a_rank"] = group_pct_rank(merged, "fnd2_ebitdm", "industry")
    merged["profit_leg_b_rank"] = group_pct_rank(merged, "fnd2_ebitfr", "industry")
    merged["profit_rank"] = merged["profit_leg_a_rank"] + merged["profit_leg_b_rank"]
    merged["asset_rank"] = group_pct_rank(merged, "fn_assets_fair_val_a", "industry")
    merged["mom_rank"] = group_pct_rank(merged, "mom_raw", "industry")
    merged["gate_passed"] = merged["asset_rank"].astype(float) > 0.5

    merged["missing_reason"] = missing_reason
    merged["score"] = 0.0
    valid_input_mask = merged["missing_reason"].eq("")
    gated_mask = valid_input_mask & merged["gate_passed"]
    merged.loc[gated_mask, "score"] = (
        merged.loc[gated_mask, "profit_rank"] - merged.loc[gated_mask, "mom_rank"]
    )

    valid_scored_symbols = int(valid_input_mask.sum())
    if valid_scored_symbols < int(min_scored_symbols):
        raise SignalGenerationError(
            "Reference-data coverage too low for profit_asset_gate: "
            f"{valid_scored_symbols} valid symbols < required {int(min_scored_symbols)}."
        )

    scores = merged[["symbol", "score", "sector"]].copy()
    diagnostics = merged[
        [
            "symbol",
            "sector",
            "industry",
            "profit_rank",
            "asset_rank",
            "mom_rank",
            "gate_passed",
            "missing_reason",
        ]
    ].copy()
    diagnostics["data_asof_date"] = asof_date.isoformat()
    diagnostics["fundamentals_effective_date"] = merged[
        "fundamentals_effective_date"
    ].astype("string")
    diagnostics["classifications_effective_date"] = merged[
        "classifications_effective_date"
    ].astype("string")
    diagnostics = diagnostics.sort_values(["missing_reason", "symbol"]).reset_index(drop=True)
    return scores, diagnostics


def compute_profit_asset_gate_proxy_scores(
    bars: pd.DataFrame,
    *,
    classifications: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    group_level: str = "auto",
    profit_window: int = 63,
    asset_window: int = 63,
    mom_window: int = 5,
    min_scored_symbols: int = 50,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    panel = compute_profit_asset_gate_proxy_panel(
        bars,
        classifications=classifications,
        sector_map=sector_map,
        group_level=group_level,
        profit_window=profit_window,
        asset_window=asset_window,
        mom_window=mom_window,
    )
    latest_date = _latest_completed_trade_date(panel)
    latest = panel[panel["trade_date"] == latest_date].copy()
    scores, diagnostics = score_profit_asset_gate_proxy_frame(
        latest,
        min_scored_symbols=min_scored_symbols,
    )
    diagnostics["data_asof_date"] = latest_date.isoformat()
    diagnostics = diagnostics.sort_values(["missing_reason", "symbol"]).reset_index(drop=True)
    return scores, diagnostics


def compute_profit_asset_gate_proxy_panel(
    bars: pd.DataFrame,
    *,
    classifications: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    group_level: str = "auto",
    profit_window: int = 63,
    asset_window: int = 63,
    mom_window: int = 5,
) -> pd.DataFrame:
    if profit_window <= 1 or asset_window <= 1 or mom_window <= 0:
        raise ValueError("Proxy windows must be positive and longer than 1 where applicable.")

    data = _normalize_bars(bars)
    data = data.sort_values(["symbol", "trade_date"]).reset_index(drop=True)
    data["returns"] = data.groupby("symbol")["c"].pct_change()
    data["profit_raw"] = data.groupby("symbol")["returns"].transform(
        lambda s: s.rolling(window=profit_window, min_periods=profit_window).mean()
        / (
            s.rolling(window=profit_window, min_periods=profit_window).std(ddof=0)
            + 1e-6
        )
    )
    data["asset_raw"] = data.groupby("symbol")["returns"].transform(
        lambda s: -s.rolling(window=asset_window, min_periods=asset_window).std(ddof=0)
    )
    data["mom_raw"] = data.groupby("symbol")["returns"].transform(
        lambda s: s.rolling(window=mom_window, min_periods=mom_window).mean()
    )

    panel = data[
        ["symbol", "trade_date", "profit_raw", "asset_raw", "mom_raw"]
    ].copy()
    panel = merge_proxy_classifications(
        panel,
        classifications=classifications,
        sector_map=sector_map,
    )
    panel["sector"] = _clean_text(panel["sector"], default="ALL")
    panel["industry"] = _clean_text(panel["industry"])
    panel["group_key"] = build_group_key(panel, group_level=group_level)
    return panel.reset_index(drop=True)


def score_profit_asset_gate_proxy_frame(
    latest: pd.DataFrame,
    *,
    min_scored_symbols: int = 50,
    asset_gate_threshold: float = 0.5,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if latest.empty:
        raise SignalGenerationError("No latest-date proxy rows available to score.")

    missing_reason = pd.Series("", index=latest.index, dtype="object")
    insufficient = latest[["profit_raw", "asset_raw", "mom_raw"]].isna().any(axis=1)
    missing_reason.loc[insufficient] = "insufficient_history"
    latest["missing_reason"] = missing_reason

    latest["profit_rank"] = group_pct_rank(latest, "profit_raw", "group_key")
    latest["asset_rank"] = group_pct_rank(latest, "asset_raw", "group_key")
    latest["mom_rank"] = group_pct_rank(latest, "mom_raw", "group_key")
    latest["gate_passed"] = latest["asset_rank"].astype(float) > float(asset_gate_threshold)
    latest["score"] = 0.0
    valid_mask = latest["missing_reason"].eq("")
    gated_mask = valid_mask & latest["gate_passed"]
    latest.loc[gated_mask, "score"] = (
        latest.loc[gated_mask, "profit_rank"] - latest.loc[gated_mask, "mom_rank"]
    )

    valid_scored_symbols = int(valid_mask.sum())
    if valid_scored_symbols < int(min_scored_symbols):
        raise SignalGenerationError(
            "Coverage too low for profit_asset_gate_proxy: "
            f"{valid_scored_symbols} valid symbols < required {int(min_scored_symbols)}."
        )

    scores = latest[["symbol", "score", "sector"]].copy()
    diagnostics = latest[
        [
            "symbol",
            "canonical_symbol",
            "sector",
            "industry",
            "group_key",
            "profit_rank",
            "asset_rank",
            "mom_rank",
            "gate_passed",
            "missing_reason",
        ]
    ].copy()
    return scores, diagnostics


def build_signal_frame(
    scores: pd.DataFrame,
    *,
    trade_date: date,
    sector_map: dict[str, str] | None = None,
    default_sector: str = "ALL",
) -> pd.DataFrame:
    if scores.empty:
        raise SignalGenerationError("Score frame is empty.")

    sectors = sector_map or {}
    out = scores.copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["score"] = pd.to_numeric(out["score"], errors="coerce")
    out = out.dropna(subset=["score"])

    if "sector" in out.columns:
        out["sector"] = _clean_text(out["sector"], default=default_sector)
    else:
        out["sector"] = out["symbol"].map(lambda s: sectors.get(s, default_sector))
        out["sector"] = _clean_text(out["sector"], default=default_sector)

    out["asof_date"] = trade_date.isoformat()
    out = out[["symbol", "score", "sector", "asof_date"]]
    out = out.sort_values("score", ascending=False).reset_index(drop=True)
    return out


def _strategy_file_from_args(cfg: object, args: argparse.Namespace) -> Path:
    strategy_file = getattr(args, "strategy_file", "") or ""
    if strategy_file:
        return Path(strategy_file).resolve()
    return selected_strategy_path(cfg.private_dir)


def _load_runtime_strategy(
    cfg: object,
    args: argparse.Namespace,
    *,
    require_approved: bool = False,
):
    path = _strategy_file_from_args(cfg, args)
    if not path.exists():
        return None, path
    strategy = load_strategy_spec(path, require_approved=require_approved)
    return strategy, path


def _resolve_requested_model(cfg: object, args: argparse.Namespace) -> str:
    explicit = getattr(args, "model", None)
    if explicit:
        return str(explicit).strip().lower()

    strategy_path = _strategy_file_from_args(cfg, args)
    if strategy_path.exists():
        try:
            strategy = load_strategy_spec(strategy_path, require_approved=True)
            if strategy.members:
                return MODEL_RESEARCH_SELECTED
        except Exception:
            pass
    return str(cfg.signal_model).strip().lower()


def run_signal_generation(args: argparse.Namespace) -> Path:
    cfg = load_config()
    if args.signals_dir:
        cfg.signals_dir = Path(args.signals_dir).resolve()
    cfg.ensure_runtime_dirs()

    trade_date = parse_trade_date(args.date)
    model = _resolve_requested_model(cfg, args)
    group_level = str(getattr(args, "group_level", "auto") or "auto").strip().lower()
    if model not in list_signal_models():
        raise SignalGenerationError(f"Unsupported model: {model}")
    if group_level not in {"industry", "sector", "auto", "market"}:
        raise SignalGenerationError(f"Unsupported group_level: {group_level}")

    universe_file = (
        Path(args.universe_file).resolve()
        if args.universe_file
        else (cfg.private_dir / "universe.csv")
    )
    sector_map_file = (
        Path(args.sector_map_file).resolve()
        if args.sector_map_file
        else (cfg.private_dir / "sector_map.csv")
    )
    fundamentals_file = (
        Path(args.fundamentals_file).resolve()
        if getattr(args, "fundamentals_file", "")
        else cfg.fundamentals_file
    )
    classifications_file = (
        Path(args.classifications_file).resolve()
        if getattr(args, "classifications_file", "")
        else cfg.classifications_file
    )

    symbols = load_universe_symbols(
        universe_file=universe_file,
        signals_dir=cfg.signals_dir,
    )
    if len(symbols) < 50:
        raise SignalGenerationError(
            f"Universe too small ({len(symbols)} symbols). Provide a broader universe."
        )

    api_key, api_secret = cfg.require_alpaca_credentials()
    broker = AlpacaBroker(api_key, api_secret, paper=True)
    lookback_days = max(5, int(args.lookback_days))
    if model == MODEL_PROFIT_ASSET_GATE_PROXY:
        lookback_days = max(120, lookback_days)
    elif model == MODEL_PROFIT_ASSET_GATE:
        lookback_days = max(180, lookback_days)
    start = trade_date - timedelta(days=lookback_days + 40)
    top_n = int(getattr(args, "top_n", None) or cfg.top_n)
    classification_snapshot_path: Path | None = None
    selected_strategy, selected_strategy_file = _load_runtime_strategy(
        cfg,
        args,
        require_approved=False,
    )

    if model == MODEL_FAILED_MOVE_VWAP:
        raw_bars = broker.get_daily_bars(
            symbols,
            start=start,
            end=trade_date,
            timeframe="1Day",
            adjustment="raw",
            limit=1000,
        )
        scores = compute_failed_move_vwap_scores(
            raw_bars,
            smoothing=max(1, int(args.smoothing)),
        )
        data_asof_date = _latest_completed_trade_date(_normalize_bars(raw_bars))
        sector_map = load_sector_map(sector_map_file)
        signal_frame = build_signal_frame(
            scores,
            trade_date=trade_date,
            sector_map=sector_map,
            default_sector="ALL",
        )
        diagnostics_path: Path | None = None
    elif model == MODEL_PROFIT_ASSET_GATE:
        raw_bars = broker.get_daily_bars(
            symbols,
            start=start,
            end=trade_date,
            timeframe="1Day",
            adjustment="split,spin-off",
            limit=1000,
        )
        normalized_bars = _normalize_bars(raw_bars)
        data_asof_date = _latest_completed_trade_date(normalized_bars)
        try:
            fundamentals = load_fundamentals_asof(
                symbols,
                data_asof_date,
                fundamentals_file,
                freshness_days=180,
            )
            classifications = load_classifications_asof(
                symbols,
                data_asof_date,
                classifications_file,
            )
        except ReferenceDataError as exc:
            raise SignalGenerationError(str(exc)) from exc

        scores, diagnostics = compute_profit_asset_gate_scores(
            symbols,
            normalized_bars,
            fundamentals=fundamentals,
            classifications=classifications,
            asof_date=data_asof_date,
            min_scored_symbols=max(200, 4 * top_n),
        )
        signal_frame = build_signal_frame(
            scores,
            trade_date=trade_date,
            default_sector="UNMAPPED",
        )
        diagnostics_path = (
            cfg.tmp_dir / f"signal_diagnostics_{model}_{trade_date.isoformat()}.csv"
        )
        diagnostics.to_csv(diagnostics_path, index=False)
    elif model == MODEL_PROFIT_ASSET_GATE_PROXY:
        raw_bars = broker.get_daily_bars(
            symbols,
            start=start,
            end=trade_date,
            timeframe="1Day",
            adjustment="split,spin-off",
            limit=1000,
        )
        sector_map = load_sector_map(sector_map_file)
        data_asof_date = _latest_completed_trade_date(_normalize_bars(raw_bars))
        classifications, classification_snapshot_path = load_cached_classifications(
            cfg.reference_dir,
            snapshot_date=data_asof_date,
            required=True,
        )
        scores, diagnostics = compute_profit_asset_gate_proxy_scores(
            raw_bars,
            classifications=classifications,
            sector_map=sector_map,
            group_level=group_level,
            profit_window=63,
            asset_window=63,
            mom_window=5,
            min_scored_symbols=max(50, 2 * top_n),
        )
        signal_frame = build_signal_frame(
            scores,
            trade_date=trade_date,
            default_sector="ALL",
        )
        diagnostics_path = (
            cfg.tmp_dir / f"signal_diagnostics_{model}_{trade_date.isoformat()}.csv"
        )
        diagnostics.to_csv(diagnostics_path, index=False)
    else:
        raw_bars = broker.get_daily_bars(
            symbols,
            start=start,
            end=trade_date,
            timeframe="1Day",
            adjustment="split,spin-off",
            limit=1000,
        )
        sector_map = load_sector_map(sector_map_file)
        data_asof_date = _latest_completed_trade_date(_normalize_bars(raw_bars))
        classifications, classification_snapshot_path = load_cached_classifications(
            cfg.reference_dir,
            snapshot_date=data_asof_date,
            required=True,
        )

        # Respect promoted runtime defaults when the selected strategy is being used.
        runtime_group_level = group_level
        if model == MODEL_RESEARCH_SELECTED:
            if selected_strategy is None:
                raise SignalGenerationError(
                    f"Selected strategy file not found: {selected_strategy_file}"
                )
            selected_strategy = load_strategy_spec(selected_strategy_file, require_approved=True)
            runtime_group_level = selected_strategy.group_level

        required_count = max(50, 2 * top_n)
        if model == MODEL_RESEARCH_SELECTED:
            member_panels: list[tuple[dict[str, object], pd.DataFrame]] = []
            member_diags: list[pd.DataFrame] = []
            for member in selected_strategy.members:
                panel, diagnostics = compute_alpha_score_panel(
                    member.alpha_name,
                    raw_bars,
                    classifications=classifications,
                    sector_map=sector_map,
                    group_level=member.group_level,
                    params=member.params,
                    signal_decay=member.signal_decay,
                    score_truncation=member.score_truncation,
                    min_scored_symbols=max(50, 2 * int(member.top_n)),
                )
                if panel.empty:
                    continue
                latest_trade_date = panel["trade_date"].max()
                member_latest = panel[panel["trade_date"] == latest_trade_date].copy()
                member_panels.append((member.to_dict(), member_latest))
                if not diagnostics.empty:
                    latest_diag = diagnostics[diagnostics["trade_date"] == latest_trade_date].copy()
                    latest_diag["member_name"] = member.name
                    latest_diag["alpha_name"] = member.alpha_name
                    latest_diag["member_weight"] = member.weight
                    member_diags.append(latest_diag)
            combined = combine_strategy_score_panels(member_panels)
            if combined.empty:
                raise SignalGenerationError("Selected strategy produced no member scores.")
            latest_trade_date = combined["trade_date"].max()
            latest_scores = combined[combined["trade_date"] == latest_trade_date].copy()
            if len(latest_scores) < required_count:
                raise SignalGenerationError(
                    "Coverage too low for selected strategy: "
                    f"{len(latest_scores)} valid symbols < required {required_count}."
                )
            scores = latest_scores[["symbol", "score", "sector"]].copy()
            diagnostics = (
                pd.concat(member_diags, ignore_index=True)
                if member_diags
                else pd.DataFrame(columns=["symbol", "member_name", "alpha_name", "member_weight"])
            )
        else:
            definition = get_alpha_definition(model)
            score_panel, diagnostics = compute_alpha_score_panel(
                definition.name,
                raw_bars,
                classifications=classifications,
                sector_map=sector_map,
                group_level=runtime_group_level,
                params=definition.default_params,
                signal_decay=int(getattr(args, "signal_decay", 0) or 0),
                score_truncation=(
                    None
                    if getattr(args, "score_truncation", None) in {"", None}
                    else float(getattr(args, "score_truncation"))
                ),
                min_scored_symbols=required_count,
            )
            if score_panel.empty:
                raise SignalGenerationError(f"Research alpha {model} produced no score panel.")
            latest_trade_date = score_panel["trade_date"].max()
            latest_scores = score_panel[score_panel["trade_date"] == latest_trade_date].copy()
            diagnostics = (
                diagnostics[diagnostics["trade_date"] == latest_trade_date].copy()
                if not diagnostics.empty
                else pd.DataFrame()
            )
            scores = latest_scores[["symbol", "score", "sector"]].copy()

        signal_frame = build_signal_frame(
            scores,
            trade_date=trade_date,
            default_sector="ALL",
        )
        diagnostics_path = (
            cfg.tmp_dir / f"signal_diagnostics_{model}_{trade_date.isoformat()}.csv"
        )
        diagnostics.to_csv(diagnostics_path, index=False)

    output_path = (
        Path(args.output_file).resolve()
        if args.output_file
        else signal_path_for_date(cfg.signals_dir, trade_date)
    )
    output_path.parent.mkdir(parents=True, exist_ok=True)
    signal_frame.to_csv(output_path, index=False)

    print(f"signal_file: {output_path}")
    print(f"trade_date: {trade_date.isoformat()}")
    print(f"data_asof_date: {data_asof_date.isoformat()}")
    print(f"universe_count: {len(symbols)}")
    print(f"scored_count: {len(signal_frame)}")
    print(f"model: {model}")
    if classification_snapshot_path is not None:
        print(f"classification_snapshot_file: {classification_snapshot_path}")
    if model == MODEL_RESEARCH_SELECTED and selected_strategy_file.exists():
        print(f"selected_strategy_file: {selected_strategy_file}")
    if diagnostics_path is not None:
        print(f"diagnostics_file: {diagnostics_path}")
    print("top5:")
    print(signal_frame.head(5).to_string(index=False))
    return output_path


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Generate daily Alpaca signal CSV from Alpaca market and reference data."
    )
    parser.add_argument("--date", default="", help="Trade date in YYYY-MM-DD (ET).")
    parser.add_argument("--signals-dir", default="", help="Override signals directory.")
    parser.add_argument(
        "--output-file",
        default="",
        help="Optional explicit output CSV path.",
    )
    parser.add_argument(
        "--universe-file",
        default="",
        help="Universe CSV path (default: paper/alpaca/private/universe.csv).",
    )
    parser.add_argument(
        "--sector-map-file",
        default="",
        help="Optional sector map CSV with columns symbol,sector for legacy models.",
    )
    parser.add_argument(
        "--fundamentals-file",
        default="",
        help="Reference fundamentals CSV path for fundamental models.",
    )
    parser.add_argument(
        "--classifications-file",
        default="",
        help="Reference classifications CSV path for fundamental models.",
    )
    parser.add_argument(
        "--strategy-file",
        default="",
        help="Selected strategy JSON path for promoted research deployment.",
    )
    parser.add_argument(
        "--model",
        choices=MODEL_CHOICES,
        default=None,
        help="Signal model to generate. Defaults to ALPACA_SIGNAL_MODEL from .env.",
    )
    parser.add_argument(
        "--group-level",
        choices=["industry", "sector", "auto", "market"],
        default="auto",
        help="Grouping level for proxy and research price/volume models.",
    )
    parser.add_argument(
        "--signal-decay",
        type=int,
        default=0,
        help="Linear score decay window for research models.",
    )
    parser.add_argument(
        "--score-truncation",
        default="",
        help="Optional absolute score contribution cap for research models (e.g. 0.05).",
    )
    parser.add_argument(
        "--lookback-days",
        type=int,
        default=60,
        help="Calendar lookback days for daily bars fetch.",
    )
    parser.add_argument(
        "--smoothing",
        type=int,
        default=2,
        help="Rolling smoothing window for failed_move_vwap.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_signal_generation(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
