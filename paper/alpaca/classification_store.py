from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd


CLASSIFICATION_COLUMNS = [
    "symbol",
    "canonical_symbol",
    "snapshot_date",
    "sector",
    "industry",
    "is_delisted",
    "delisted_date",
    "original_symbol",
    "source",
]

SYMBOL_MASTER_COLUMNS = [
    "symbol",
    "canonical_symbol",
    "original_symbol",
    "first_seen_date",
    "last_seen_date",
    "is_delisted",
    "delisted_date",
    "source",
]


class ClassificationStoreError(ValueError):
    pass


def _normalize_symbol_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .str.upper()
        .replace({"": pd.NA, "<NA>": pd.NA, "NAN": pd.NA, "NONE": pd.NA})
    )


def _normalize_text_series(series: pd.Series) -> pd.Series:
    return (
        series.astype("string")
        .str.strip()
        .replace({"": pd.NA, "<NA>": pd.NA, "NAN": pd.NA, "NONE": pd.NA})
    )


def _normalize_date_series(series: pd.Series) -> pd.Series:
    return pd.to_datetime(series, errors="coerce").dt.date


def _ensure_columns(frame: pd.DataFrame, columns: list[str]) -> pd.DataFrame:
    out = frame.copy()
    for column in columns:
        if column not in out.columns:
            out[column] = pd.NA
    return out[columns].copy()


def classifications_snapshot_path(reference_dir: Path, snapshot_date: date) -> Path:
    return reference_dir / "classifications" / f"{snapshot_date.isoformat()}.csv"


def classifications_latest_path(reference_dir: Path) -> Path:
    return reference_dir / "classifications_latest.csv"


def symbol_master_path(reference_dir: Path) -> Path:
    return reference_dir / "symbol_master.csv"


def _read_csv(path: Path, required_columns: list[str], label: str) -> pd.DataFrame:
    if not path.exists():
        raise ClassificationStoreError(f"{label} file not found: {path}")

    frame = pd.read_csv(path)
    if frame.empty:
        return pd.DataFrame(columns=required_columns)

    rename = {column: str(column).strip().lower() for column in frame.columns}
    out = frame.rename(columns=rename).copy()
    missing = [column for column in required_columns if column not in out.columns]
    if missing:
        joined = ", ".join(missing)
        raise ClassificationStoreError(
            f"{label} file missing required columns: {joined}"
        )
    return out[required_columns].copy()


def _discover_snapshot_paths(reference_dir: Path) -> list[tuple[date, Path]]:
    paths: list[tuple[date, Path]] = []
    snapshot_dir = reference_dir / "classifications"
    if snapshot_dir.exists():
        for path in snapshot_dir.glob("*.csv"):
            try:
                parsed = pd.Timestamp(path.stem).date()
            except Exception:
                continue
            paths.append((parsed, path))
    latest_path = classifications_latest_path(reference_dir)
    if latest_path.exists():
        try:
            latest_frame = pd.read_csv(latest_path, nrows=1)
            if "snapshot_date" in latest_frame.columns and not latest_frame.empty:
                parsed = pd.Timestamp(latest_frame.iloc[0]["snapshot_date"]).date()
                paths.append((parsed, latest_path))
        except Exception:
            pass
    deduped: dict[tuple[date, str], Path] = {}
    for snapshot_date, path in paths:
        deduped[(snapshot_date, str(path.resolve()))] = path
    return sorted([(key[0], value) for key, value in deduped.items()], key=lambda item: item[0])


def resolve_classification_snapshot_path(
    reference_dir: Path,
    *,
    snapshot_date: date | None = None,
) -> Path:
    discovered = _discover_snapshot_paths(reference_dir)
    if not discovered:
        raise ClassificationStoreError(
            "No cached classification snapshot found. Run classification_sync.py first."
        )

    if snapshot_date is None:
        return discovered[-1][1]

    eligible = [path for stamp, path in discovered if stamp <= snapshot_date]
    if eligible:
        return eligible[-1]
    return discovered[-1][1]


def load_classifications_snapshot(
    reference_dir: Path,
    *,
    snapshot_date: date | None = None,
) -> pd.DataFrame:
    path = resolve_classification_snapshot_path(reference_dir, snapshot_date=snapshot_date)
    frame = _read_csv(path, CLASSIFICATION_COLUMNS, "classifications")
    if frame.empty:
        return frame

    frame["symbol"] = _normalize_symbol_series(frame["symbol"])
    frame["canonical_symbol"] = _normalize_symbol_series(frame["canonical_symbol"]).fillna(
        frame["symbol"]
    )
    frame["original_symbol"] = _normalize_symbol_series(frame["original_symbol"]).fillna(
        frame["symbol"]
    )
    frame["snapshot_date"] = _normalize_date_series(frame["snapshot_date"])
    frame["sector"] = _normalize_text_series(frame["sector"])
    frame["industry"] = _normalize_text_series(frame["industry"])
    frame["is_delisted"] = frame["is_delisted"].fillna(False).astype(bool)
    frame["delisted_date"] = _normalize_date_series(frame["delisted_date"])
    frame["source"] = _normalize_text_series(frame["source"]).fillna("fmp")
    return frame.dropna(subset=["symbol"]).reset_index(drop=True)


def load_symbol_master(reference_dir: Path) -> pd.DataFrame:
    path = symbol_master_path(reference_dir)
    frame = _read_csv(path, SYMBOL_MASTER_COLUMNS, "symbol master")
    if frame.empty:
        return frame

    frame["symbol"] = _normalize_symbol_series(frame["symbol"])
    frame["canonical_symbol"] = _normalize_symbol_series(frame["canonical_symbol"]).fillna(
        frame["symbol"]
    )
    frame["original_symbol"] = _normalize_symbol_series(frame["original_symbol"]).fillna(
        frame["symbol"]
    )
    frame["first_seen_date"] = _normalize_date_series(frame["first_seen_date"])
    frame["last_seen_date"] = _normalize_date_series(frame["last_seen_date"])
    frame["is_delisted"] = frame["is_delisted"].fillna(False).astype(bool)
    frame["delisted_date"] = _normalize_date_series(frame["delisted_date"])
    frame["source"] = _normalize_text_series(frame["source"]).fillna("fmp")
    return frame.dropna(subset=["symbol"]).reset_index(drop=True)


def _extract_profile_frame(
    profiles: pd.DataFrame,
    *,
    snapshot_date: date,
) -> pd.DataFrame:
    if profiles.empty:
        return pd.DataFrame(
            columns=["symbol", "sector", "industry", "snapshot_date", "source"]
        )

    rename = {column: str(column).strip().lower() for column in profiles.columns}
    out = profiles.rename(columns=rename).copy()
    symbol_column = "symbol" if "symbol" in out.columns else None
    if symbol_column is None:
        raise ClassificationStoreError("Profile payload is missing symbol.")

    out["symbol"] = _normalize_symbol_series(out[symbol_column])
    out["sector"] = _normalize_text_series(out.get("sector", pd.Series(dtype="object")))
    out["industry"] = _normalize_text_series(
        out.get("industry", pd.Series(dtype="object"))
    )
    out["snapshot_date"] = snapshot_date
    if "source" in out.columns:
        out["source"] = _normalize_text_series(out["source"]).fillna("fmp")
    else:
        out["source"] = "fmp"
    out = out.dropna(subset=["symbol"]).drop_duplicates(subset=["symbol"], keep="first")
    return out[["symbol", "sector", "industry", "snapshot_date", "source"]].reset_index(
        drop=True
    )


def _extract_symbol_change_frame(changes: pd.DataFrame) -> pd.DataFrame:
    if changes.empty:
        return pd.DataFrame(columns=["symbol", "canonical_symbol", "change_date"])

    rename = {column: str(column).strip().lower() for column in changes.columns}
    out = changes.rename(columns=rename).copy()
    old_column = next(
        (name for name in ["oldsymbol", "old_symbol", "oldticker", "old_ticker"] if name in out.columns),
        None,
    )
    new_column = next(
        (name for name in ["newsymbol", "new_symbol", "newticker", "new_ticker"] if name in out.columns),
        None,
    )
    if old_column is None or new_column is None:
        return pd.DataFrame(columns=["symbol", "canonical_symbol", "change_date"])

    date_column = next(
        (name for name in ["date", "changedate", "change_date"] if name in out.columns),
        None,
    )
    out["symbol"] = _normalize_symbol_series(out[old_column])
    out["canonical_symbol"] = _normalize_symbol_series(out[new_column])
    if date_column is not None:
        out["change_date"] = _normalize_date_series(out[date_column])
    else:
        out["change_date"] = pd.NaT
    out = out.dropna(subset=["symbol", "canonical_symbol"])
    out = out.drop_duplicates(subset=["symbol", "canonical_symbol"], keep="last")
    return out[["symbol", "canonical_symbol", "change_date"]].reset_index(drop=True)


def _extract_delisted_frame(delisted: pd.DataFrame) -> pd.DataFrame:
    if delisted.empty:
        return pd.DataFrame(columns=["symbol", "delisted_date", "is_delisted"])

    rename = {column: str(column).strip().lower() for column in delisted.columns}
    out = delisted.rename(columns=rename).copy()
    symbol_column = next(
        (name for name in ["symbol", "ticker"] if name in out.columns),
        None,
    )
    if symbol_column is None:
        return pd.DataFrame(columns=["symbol", "delisted_date", "is_delisted"])

    date_column = next(
        (
            name
            for name in [
                "delisteddate",
                "delisted_date",
                "date",
                "ipo_date",
            ]
            if name in out.columns
        ),
        None,
    )
    out["symbol"] = _normalize_symbol_series(out[symbol_column])
    if date_column is not None:
        out["delisted_date"] = _normalize_date_series(out[date_column])
    else:
        out["delisted_date"] = pd.NaT
    out["is_delisted"] = True
    out = out.dropna(subset=["symbol"])
    out = out.sort_values(["symbol", "delisted_date"]).drop_duplicates(
        subset=["symbol"], keep="last"
    )
    return out[["symbol", "delisted_date", "is_delisted"]].reset_index(drop=True)


def _resolve_canonical_map(
    symbols: Iterable[str],
    changes: pd.DataFrame,
) -> dict[str, str]:
    next_map = {
        str(row["symbol"]).strip().upper(): str(row["canonical_symbol"]).strip().upper()
        for _, row in changes.iterrows()
        if str(row["symbol"]).strip() and str(row["canonical_symbol"]).strip()
    }

    def resolve(symbol: str) -> str:
        current = str(symbol).strip().upper()
        seen: set[str] = set()
        while current in next_map and current not in seen:
            seen.add(current)
            current = next_map[current]
        return current

    return {str(symbol).strip().upper(): resolve(str(symbol)) for symbol in symbols}


def build_classification_snapshot(
    profiles: pd.DataFrame,
    symbol_changes: pd.DataFrame,
    delisted: pd.DataFrame,
    *,
    snapshot_date: date,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    profiles_norm = _extract_profile_frame(profiles, snapshot_date=snapshot_date)
    changes_norm = _extract_symbol_change_frame(symbol_changes)
    delisted_norm = _extract_delisted_frame(delisted)

    all_symbols = set(profiles_norm["symbol"].dropna().tolist())
    all_symbols.update(changes_norm["symbol"].dropna().tolist())
    all_symbols.update(changes_norm["canonical_symbol"].dropna().tolist())
    all_symbols.update(delisted_norm["symbol"].dropna().tolist())
    canonical_map = _resolve_canonical_map(sorted(all_symbols), changes_norm)

    symbol_master = pd.DataFrame({"symbol": sorted(all_symbols)})
    if symbol_master.empty:
        return (
            pd.DataFrame(columns=CLASSIFICATION_COLUMNS),
            pd.DataFrame(columns=SYMBOL_MASTER_COLUMNS),
        )

    symbol_master["canonical_symbol"] = symbol_master["symbol"].map(
        lambda symbol: canonical_map.get(symbol, symbol)
    )
    symbol_master["original_symbol"] = symbol_master["symbol"]
    symbol_master["source"] = "fmp"

    profile_lookup = profiles_norm.rename(columns={"symbol": "canonical_symbol"})
    symbol_master = symbol_master.merge(
        profile_lookup[["canonical_symbol", "sector", "industry", "source"]],
        on="canonical_symbol",
        how="left",
        suffixes=("", "_profile"),
    )
    if "source_profile" in symbol_master.columns:
        symbol_master["source"] = _normalize_text_series(
            symbol_master["source_profile"]
        ).fillna(symbol_master["source"])
        symbol_master = symbol_master.drop(columns=["source_profile"])

    delisted_lookup = delisted_norm.rename(columns={"symbol": "lookup_symbol"})
    symbol_master = symbol_master.merge(
        delisted_lookup,
        left_on="symbol",
        right_on="lookup_symbol",
        how="left",
    )
    symbol_master["is_delisted"] = symbol_master["is_delisted"].where(
        symbol_master["is_delisted"].notna(),
        False,
    ).astype(bool)
    symbol_master["delisted_date"] = _normalize_date_series(symbol_master["delisted_date"])
    symbol_master["first_seen_date"] = snapshot_date
    symbol_master["last_seen_date"] = snapshot_date
    symbol_master = symbol_master.drop(columns=["lookup_symbol"], errors="ignore")

    snapshot = symbol_master.copy()
    snapshot["snapshot_date"] = snapshot_date
    snapshot = _ensure_columns(snapshot, CLASSIFICATION_COLUMNS)

    snapshot["symbol"] = _normalize_symbol_series(snapshot["symbol"])
    snapshot["canonical_symbol"] = _normalize_symbol_series(snapshot["canonical_symbol"]).fillna(
        snapshot["symbol"]
    )
    snapshot["original_symbol"] = _normalize_symbol_series(snapshot["original_symbol"]).fillna(
        snapshot["symbol"]
    )
    snapshot["sector"] = _normalize_text_series(snapshot["sector"])
    snapshot["industry"] = _normalize_text_series(snapshot["industry"])
    snapshot["snapshot_date"] = _normalize_date_series(snapshot["snapshot_date"])
    snapshot["is_delisted"] = snapshot["is_delisted"].where(
        snapshot["is_delisted"].notna(),
        False,
    ).astype(bool)
    snapshot["delisted_date"] = _normalize_date_series(snapshot["delisted_date"])
    snapshot["source"] = _normalize_text_series(snapshot["source"]).fillna("fmp")
    snapshot = snapshot.dropna(subset=["symbol"]).drop_duplicates(
        subset=["symbol"], keep="first"
    )

    symbol_master = _ensure_columns(symbol_master, SYMBOL_MASTER_COLUMNS)
    symbol_master = symbol_master.dropna(subset=["symbol"]).drop_duplicates(
        subset=["symbol"], keep="first"
    )
    return (
        snapshot.reset_index(drop=True),
        symbol_master.reset_index(drop=True),
    )


def write_classification_snapshot(
    snapshot: pd.DataFrame,
    symbol_master: pd.DataFrame,
    *,
    reference_dir: Path,
    snapshot_date: date,
) -> tuple[Path, Path, Path]:
    reference_dir.mkdir(parents=True, exist_ok=True)
    (reference_dir / "classifications").mkdir(parents=True, exist_ok=True)

    snapshot_clean = _ensure_columns(snapshot, CLASSIFICATION_COLUMNS).copy()
    snapshot_clean.to_csv(
        classifications_snapshot_path(reference_dir, snapshot_date),
        index=False,
    )
    snapshot_clean.to_csv(classifications_latest_path(reference_dir), index=False)

    symbol_master_clean = _ensure_columns(symbol_master, SYMBOL_MASTER_COLUMNS).copy()
    symbol_master_clean.to_csv(symbol_master_path(reference_dir), index=False)
    return (
        classifications_snapshot_path(reference_dir, snapshot_date),
        classifications_latest_path(reference_dir),
        symbol_master_path(reference_dir),
    )


def merge_proxy_classifications(
    base: pd.DataFrame,
    *,
    classifications: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
) -> pd.DataFrame:
    out = base.copy()
    if "symbol" not in out.columns:
        raise ClassificationStoreError("merge_proxy_classifications requires a symbol column.")

    out["symbol"] = _normalize_symbol_series(out["symbol"])
    class_frame = (
        classifications.copy()
        if classifications is not None
        else pd.DataFrame(columns=CLASSIFICATION_COLUMNS)
    )
    if not class_frame.empty:
        class_frame["symbol"] = _normalize_symbol_series(class_frame["symbol"])
        class_frame["canonical_symbol"] = _normalize_symbol_series(
            class_frame["canonical_symbol"]
        ).fillna(class_frame["symbol"])
        class_frame["sector"] = _normalize_text_series(class_frame["sector"])
        class_frame["industry"] = _normalize_text_series(class_frame["industry"])
        out = out.merge(
            class_frame[["symbol", "canonical_symbol", "sector", "industry"]],
            on="symbol",
            how="left",
        )
    else:
        out["canonical_symbol"] = out["symbol"]
        out["sector"] = pd.NA
        out["industry"] = pd.NA

    if sector_map:
        out["sector"] = out["sector"].combine_first(
            out["symbol"].map(lambda symbol: sector_map.get(str(symbol).upper(), pd.NA))
        )

    out["canonical_symbol"] = _normalize_symbol_series(out["canonical_symbol"]).fillna(
        out["symbol"]
    )
    out["sector"] = _normalize_text_series(out["sector"])
    out["industry"] = _normalize_text_series(out["industry"])
    return out.reset_index(drop=True)


def build_group_key(
    frame: pd.DataFrame,
    *,
    group_level: str = "auto",
) -> pd.Series:
    group_level_norm = str(group_level).strip().lower() or "auto"
    sector = _normalize_text_series(frame.get("sector", pd.Series(dtype="object")))
    industry = _normalize_text_series(frame.get("industry", pd.Series(dtype="object")))

    if group_level_norm == "market":
        out = pd.Series(["MARKET"] * len(frame), index=frame.index, dtype="string")
    elif group_level_norm == "industry":
        out = industry.fillna("ALL")
    elif group_level_norm == "sector":
        out = sector.fillna("ALL")
    else:
        out = industry.combine_first(sector).fillna("ALL")
    return out.astype("string")
