from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import date
import hashlib
import json
from pathlib import Path
from typing import Any

import pandas as pd

from backtest_engine import SplitWindows


def _stable_digest(payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str).encode("utf-8")
    return hashlib.sha256(blob).hexdigest()[:20]


def _date_list(values: list[date]) -> list[str]:
    return [value.isoformat() for value in values]


def _to_date_list(values: list[str]) -> list[date]:
    return [pd.Timestamp(value).date() for value in values]


@dataclass(frozen=True, slots=True)
class PreparedInputsMeta:
    prepared_key: str
    end_date: str
    classification_snapshot_path: str
    classification_snapshot_date: str
    feed: str
    train_days: int
    oos_days: int
    test_days: int
    min_universe: int
    min_universe_ratio: float
    degraded_depth: bool
    coverage_ratio: float
    latest_completed_date: str
    usable_end_date: str
    train_dates: list[str]
    oos_dates: list[str]
    test_dates: list[str]


class ResearchMaterializedCache:
    def __init__(self, cache_dir: Path) -> None:
        self.root = cache_dir / "research_materialized"
        self.prepared_root = self.root / "prepared"
        self.score_root = self.root / "score_panels"
        self.prepared_root.mkdir(parents=True, exist_ok=True)
        self.score_root.mkdir(parents=True, exist_ok=True)

    def build_prepared_key(
        self,
        *,
        end_date: date,
        classification_snapshot_path: Path,
        classification_snapshot_date: date,
        feed: str,
        train_days: int,
        oos_days: int,
        test_days: int,
        min_universe: int,
        min_universe_ratio: float,
    ) -> str:
        return _stable_digest(
            {
                "end_date": end_date.isoformat(),
                "classification_snapshot_path": str(classification_snapshot_path.resolve()),
                "classification_snapshot_date": classification_snapshot_date.isoformat(),
                "feed": str(feed).strip().lower(),
                "train_days": int(train_days),
                "oos_days": int(oos_days),
                "test_days": int(test_days),
                "min_universe": int(min_universe),
                "min_universe_ratio": float(min_universe_ratio),
                "version": 1,
            }
        )

    def prepared_dir(self, prepared_key: str) -> Path:
        return self.prepared_root / prepared_key

    def score_dir(self, prepared_key: str) -> Path:
        return self.score_root / prepared_key

    def load_prepared_inputs(self, prepared_key: str) -> dict[str, Any] | None:
        run_dir = self.prepared_dir(prepared_key)
        meta_path = run_dir / "meta.json"
        bars_path = run_dir / "bars.pkl"
        open_returns_path = run_dir / "open_returns.pkl"
        execution_map_path = run_dir / "execution_map.pkl"
        universe_lookup_path = run_dir / "universe_lookup.pkl"
        if not all(path.exists() for path in [meta_path, bars_path, open_returns_path, execution_map_path, universe_lookup_path]):
            return None

        meta = PreparedInputsMeta(**json.loads(meta_path.read_text(encoding="utf-8")))
        universe_frame = pd.read_pickle(universe_lookup_path)
        universe_lookup: dict[date, pd.DataFrame] = {}
        if not universe_frame.empty:
            universe_frame["signal_date"] = pd.to_datetime(universe_frame["signal_date"], errors="coerce").dt.date
            for signal_date, frame in universe_frame.groupby("signal_date", sort=True):
                universe_lookup[signal_date] = frame.drop(columns=["signal_date"]).reset_index(drop=True)

        return {
            "prepared_cache_key": meta.prepared_key,
            "classification_snapshot_path": Path(meta.classification_snapshot_path),
            "classification_snapshot_date": meta.classification_snapshot_date,
            "bars": pd.read_pickle(bars_path),
            "open_returns": pd.read_pickle(open_returns_path),
            "execution_map": pd.read_pickle(execution_map_path),
            "universe_lookup": universe_lookup,
            "splits": SplitWindows(
                latest_completed_date=pd.Timestamp(meta.latest_completed_date).date(),
                usable_end_date=pd.Timestamp(meta.usable_end_date).date(),
                train_dates=_to_date_list(meta.train_dates),
                oos_dates=_to_date_list(meta.oos_dates),
                test_dates=_to_date_list(meta.test_dates),
            ),
            "coverage_ratio": float(meta.coverage_ratio),
            "degraded_depth": bool(meta.degraded_depth),
        }

    def save_prepared_inputs(self, prepared: dict[str, Any]) -> str:
        prepared_key = str(prepared["prepared_cache_key"])
        run_dir = self.prepared_dir(prepared_key)
        run_dir.mkdir(parents=True, exist_ok=True)
        meta = PreparedInputsMeta(
            prepared_key=prepared_key,
            end_date=str(prepared["end_date"]),
            classification_snapshot_path=str(prepared["classification_snapshot_path"]),
            classification_snapshot_date=str(prepared["classification_snapshot_date"]),
            feed=str(prepared["feed"]),
            train_days=len(prepared["splits"].train_dates),
            oos_days=len(prepared["splits"].oos_dates),
            test_days=len(prepared["splits"].test_dates),
            min_universe=int(prepared["min_universe"]),
            min_universe_ratio=float(prepared["min_universe_ratio"]),
            degraded_depth=bool(prepared["degraded_depth"]),
            coverage_ratio=float(prepared["coverage_ratio"]),
            latest_completed_date=prepared["splits"].latest_completed_date.isoformat(),
            usable_end_date=prepared["splits"].usable_end_date.isoformat(),
            train_dates=_date_list(prepared["splits"].train_dates),
            oos_dates=_date_list(prepared["splits"].oos_dates),
            test_dates=_date_list(prepared["splits"].test_dates),
        )
        (run_dir / "meta.json").write_text(json.dumps(asdict(meta), indent=2), encoding="utf-8")
        prepared["bars"].to_pickle(run_dir / "bars.pkl")
        prepared["open_returns"].to_pickle(run_dir / "open_returns.pkl")
        prepared["execution_map"].to_pickle(run_dir / "execution_map.pkl")
        universe_rows: list[pd.DataFrame] = []
        for signal_date, frame in prepared["universe_lookup"].items():
            part = frame.copy()
            part["signal_date"] = pd.Timestamp(signal_date).date()
            universe_rows.append(part)
        universe_frame = pd.concat(universe_rows, ignore_index=True) if universe_rows else pd.DataFrame()
        universe_frame.to_pickle(run_dir / "universe_lookup.pkl")
        return prepared_key

    def build_score_key(
        self,
        *,
        prepared_key: str,
        alpha_name: str,
        params: dict[str, Any],
        group_level: str,
        signal_decay: int,
        score_truncation: float | None,
        min_scored_symbols: int,
    ) -> str:
        return _stable_digest(
            {
                "prepared_key": prepared_key,
                "alpha_name": str(alpha_name).strip().lower(),
                "params": params,
                "group_level": str(group_level).strip().lower(),
                "signal_decay": int(signal_decay),
                "score_truncation": score_truncation,
                "min_scored_symbols": int(min_scored_symbols),
                "version": 1,
            }
        )

    def load_score_panel(self, prepared_key: str, score_key: str) -> pd.DataFrame | None:
        path = self.score_dir(prepared_key) / f"{score_key}.pkl"
        if not path.exists():
            return None
        return pd.read_pickle(path)

    def save_score_panel(self, prepared_key: str, score_key: str, score_panel: pd.DataFrame) -> Path:
        out_dir = self.score_dir(prepared_key)
        out_dir.mkdir(parents=True, exist_ok=True)
        path = out_dir / f"{score_key}.pkl"
        score_panel.to_pickle(path)
        return path
