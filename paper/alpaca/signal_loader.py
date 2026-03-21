from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


REQUIRED_COLUMNS = {"symbol", "score", "sector"}
OPTIONAL_COLUMNS = {"asof_date"}


class SignalValidationError(ValueError):
    pass


def signal_path_for_date(signals_dir: Path, trade_date: date) -> Path:
    return signals_dir / f"{trade_date.isoformat()}.csv"


def load_signal_file(path: Path, trade_date: date | None = None) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Signal file not found: {path}")
    frame = pd.read_csv(path)
    return validate_signal_frame(frame, trade_date=trade_date)


def validate_signal_frame(
    frame: pd.DataFrame,
    trade_date: date | None = None,
) -> pd.DataFrame:
    if frame.empty:
        raise SignalValidationError("Signal file is empty.")

    rename = {col: str(col).strip().lower() for col in frame.columns}
    out = frame.rename(columns=rename).copy()

    missing = sorted(REQUIRED_COLUMNS - set(out.columns))
    if missing:
        raise SignalValidationError(
            f"Signal file missing required columns: {', '.join(missing)}"
        )

    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out["sector"] = out["sector"].astype(str).str.strip()
    out["score"] = pd.to_numeric(out["score"], errors="coerce")

    if out["symbol"].eq("").any():
        raise SignalValidationError("Signal file contains empty symbols.")
    if out["sector"].eq("").any():
        raise SignalValidationError("Signal file contains empty sectors.")
    if out["score"].isna().any():
        raise SignalValidationError("Signal file contains non-numeric scores.")

    duplicated = out["symbol"][out["symbol"].duplicated()].unique().tolist()
    if duplicated:
        sample = ", ".join(sorted(duplicated)[:10])
        raise SignalValidationError(
            f"Signal file contains duplicate symbols after normalization: {sample}"
        )

    if "asof_date" in out.columns:
        parsed = pd.to_datetime(out["asof_date"], errors="coerce").dt.date
        if parsed.isna().any():
            raise SignalValidationError("Signal file has invalid asof_date values.")
        if trade_date is not None and not parsed.eq(trade_date).all():
            raise SignalValidationError(
                "Signal file asof_date does not match requested trade date "
                f"{trade_date.isoformat()}."
            )
        out["asof_date"] = parsed.astype(str)

    keep = ["symbol", "score", "sector"]
    if "asof_date" in out.columns:
        keep.append("asof_date")

    out = out[keep].sort_values("score", ascending=False).reset_index(drop=True)
    return out


def select_long_short_candidates(
    signals: pd.DataFrame,
    top_n: int,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    if top_n <= 0:
        raise ValueError("top_n must be positive")

    ranked = signals.copy()
    ranked["symbol"] = ranked["symbol"].astype(str).str.strip().str.upper()
    ranked["score"] = pd.to_numeric(ranked["score"], errors="coerce")
    ranked = ranked.dropna(subset=["symbol", "score"]).drop_duplicates(
        subset=["symbol"],
        keep="first",
    )

    longs = ranked.sort_values(["score", "symbol"], ascending=[False, True]).head(top_n).copy()
    excluded = set(longs["symbol"].tolist())
    short_pool = ranked[~ranked["symbol"].isin(excluded)].copy()
    shorts = short_pool.sort_values(["score", "symbol"], ascending=[True, True]).head(top_n).copy()
    return longs.reset_index(drop=True), shorts.reset_index(drop=True)
