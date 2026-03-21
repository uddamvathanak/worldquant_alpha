from __future__ import annotations

from datetime import date
from pathlib import Path

import pandas as pd


FUNDAMENTAL_COLUMNS = [
    "symbol",
    "effective_date",
    "fnd2_ebitdm",
    "fnd2_ebitfr",
    "fn_assets_fair_val_a",
]
CLASSIFICATION_COLUMNS = [
    "symbol",
    "effective_date",
    "sector",
    "industry",
]


class ReferenceDataError(ValueError):
    pass


def _read_reference_csv(
    path: Path,
    *,
    required_columns: list[str],
    label: str,
) -> pd.DataFrame:
    if not path.exists():
        raise ReferenceDataError(f"{label} file not found: {path}")

    frame = pd.read_csv(path)
    if frame.empty:
        return pd.DataFrame(columns=required_columns)

    rename = {col: str(col).strip().lower() for col in frame.columns}
    out = frame.rename(columns=rename).copy()
    missing = [col for col in required_columns if col not in out.columns]
    if missing:
        joined = ", ".join(missing)
        raise ReferenceDataError(f"{label} file missing required columns: {joined}")

    out = out[required_columns].copy()
    out["symbol"] = out["symbol"].astype(str).str.strip().str.upper()
    out = out[out["symbol"] != ""].copy()
    out["effective_date"] = pd.to_datetime(
        out["effective_date"],
        errors="coerce",
    ).dt.date
    if out["effective_date"].isna().any():
        raise ReferenceDataError(f"{label} file contains invalid effective_date values.")

    duplicated = out.duplicated(subset=["symbol", "effective_date"], keep=False)
    if duplicated.any():
        sample = (
            out.loc[duplicated, ["symbol", "effective_date"]]
            .drop_duplicates()
            .head(10)
            .to_dict("records")
        )
        raise ReferenceDataError(
            f"{label} file contains duplicate symbol/effective_date rows: {sample}"
        )

    return out.sort_values(["symbol", "effective_date"]).reset_index(drop=True)


def _latest_asof_rows(
    frame: pd.DataFrame,
    *,
    symbols: list[str],
    asof_date: date,
) -> pd.DataFrame:
    normalized = [str(symbol).strip().upper() for symbol in symbols if str(symbol).strip()]
    universe = sorted(set(normalized))
    base = pd.DataFrame({"symbol": universe})
    if frame.empty or not universe:
        return base

    eligible = frame[
        frame["symbol"].isin(universe) & (frame["effective_date"] <= asof_date)
    ].copy()
    if eligible.empty:
        return base

    latest = (
        eligible.sort_values(["symbol", "effective_date"])
        .groupby("symbol", as_index=False)
        .tail(1)
        .reset_index(drop=True)
    )
    return base.merge(latest, on="symbol", how="left")


def load_fundamentals_asof(
    symbols: list[str],
    asof_date: date,
    path: Path,
    *,
    freshness_days: int = 180,
) -> pd.DataFrame:
    frame = _read_reference_csv(
        path,
        required_columns=FUNDAMENTAL_COLUMNS,
        label="fundamentals",
    )
    out = _latest_asof_rows(frame, symbols=symbols, asof_date=asof_date)
    if "effective_date" not in out.columns:
        out["effective_date"] = pd.NaT

    effective = pd.to_datetime(out["effective_date"], errors="coerce").dt.date
    stale = effective.map(
        lambda value: True
        if value is None or pd.isna(value)
        else (asof_date - value).days > int(freshness_days)
    )
    out["is_stale"] = stale.fillna(True).astype(bool)
    return out.reset_index(drop=True)


def load_classifications_asof(
    symbols: list[str],
    asof_date: date,
    path: Path,
) -> pd.DataFrame:
    frame = _read_reference_csv(
        path,
        required_columns=CLASSIFICATION_COLUMNS,
        label="classifications",
    )
    out = _latest_asof_rows(frame, symbols=symbols, asof_date=asof_date)
    if "sector" not in out.columns:
        out["sector"] = pd.NA
    if "industry" not in out.columns:
        out["industry"] = pd.NA
    return out.reset_index(drop=True)


def group_pct_rank(
    frame: pd.DataFrame,
    value_col: str,
    group_col: str,
) -> pd.Series:
    out = pd.Series(index=frame.index, dtype="float64")
    if frame.empty:
        return out

    valid_groups = (
        frame[group_col]
        .astype(str)
        .str.strip()
        .replace({"": pd.NA, "<NA>": pd.NA, "nan": pd.NA, "None": pd.NA})
    )
    numeric_values = pd.to_numeric(frame[value_col], errors="coerce")
    valid_mask = valid_groups.notna() & numeric_values.notna()
    if not valid_mask.any():
        return out

    ranked = (
        pd.DataFrame(
            {
                "_group": valid_groups.loc[valid_mask],
                "_value": numeric_values.loc[valid_mask],
            }
        )
        .groupby("_group")["_value"]
        .rank(method="average", pct=True)
        .astype("float64")
    )
    out.loc[ranked.index] = ranked
    return out
