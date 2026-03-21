from __future__ import annotations

import argparse
from datetime import date
import os
from pathlib import Path
import time
from typing import Any

import pandas as pd
import requests

from classification_store import (
    build_classification_snapshot,
    write_classification_snapshot,
)
from config import load_config, parse_trade_date


class ClassificationSyncError(RuntimeError):
    pass


def _read_universe_symbols(path: Path) -> list[str]:
    if not path.exists():
        raise ClassificationSyncError(
            f"Universe file not found for profile fallback: {path}"
        )
    frame = pd.read_csv(path)
    if frame.empty:
        raise ClassificationSyncError(
            f"Universe file is empty for profile fallback: {path}"
        )
    lower = {str(column).strip().lower(): column for column in frame.columns}
    symbol_column = lower.get("symbol")
    if not symbol_column:
        raise ClassificationSyncError(
            f"Universe file must include a symbol column for profile fallback: {path}"
        )
    symbols = (
        frame[symbol_column]
        .astype(str)
        .str.strip()
        .str.upper()
        .replace("", pd.NA)
        .dropna()
        .tolist()
    )
    unique = sorted(set(symbols))
    if not unique:
        raise ClassificationSyncError(
            f"Universe file had no usable symbols for profile fallback: {path}"
        )
    return unique


def _normalize_sec_symbol(symbol: str) -> str:
    return str(symbol).strip().upper().replace(".", "-")


def _sec_alt_symbols(symbol: str) -> set[str]:
    raw = str(symbol).strip().upper()
    alt = _normalize_sec_symbol(raw)
    return {raw, alt, raw.replace("-", "."), raw.replace(".", "-")}


def _extract_rows(payload: Any) -> list[dict[str, Any]]:
    if isinstance(payload, list):
        return [row for row in payload if isinstance(row, dict)]
    if isinstance(payload, dict):
        for key in ["data", "results", "items", "symbols", "profiles"]:
            if isinstance(payload.get(key), list):
                return [row for row in payload[key] if isinstance(row, dict)]
    return []


def _request_json(
    session: requests.Session,
    *,
    url: str,
    params: dict[str, Any],
    timeout: int,
) -> Any:
    response = session.get(url, params=params, timeout=timeout)
    if response.status_code >= 400:
        raise ClassificationSyncError(
            f"FMP request failed ({response.status_code}): {response.text[:200]}"
        )
    try:
        return response.json()
    except Exception as exc:
        raise ClassificationSyncError(
            f"FMP response was not valid JSON for {url}"
        ) from exc


def _fetch_profile_bulk(
    session: requests.Session,
    *,
    base_url: str,
    api_key: str,
    timeout: int,
    max_parts: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for part in range(max(1, int(max_parts))):
        url = f"{base_url}/profile-bulk"
        response = session.get(
            url,
            params={"part": part, "apikey": api_key},
            timeout=timeout,
        )
        if response.status_code == 404 and part > 0:
            break
        if response.status_code >= 400:
            raise ClassificationSyncError(
                f"FMP profile-bulk failed ({response.status_code}) at part={part}: "
                f"{response.text[:200]}"
            )
        payload = response.json()
        chunk = _extract_rows(payload)
        if not chunk:
            break
        rows.extend(chunk)
    return pd.DataFrame(rows)


def _sec_owner_org_to_sector(value: str | None) -> str | None:
    text = str(value or "").strip()
    if not text:
        return None
    parts = text.split(" ", 1)
    if len(parts) == 2 and parts[0].isdigit():
        return parts[1].strip() or text
    return text


def _fetch_sec_profiles(
    *,
    symbols: list[str],
    timeout: int,
    request_delay: float,
    user_agent: str,
) -> pd.DataFrame:
    session = requests.Session()
    headers = {"User-Agent": user_agent}
    index_response = session.get(
        "https://www.sec.gov/files/company_tickers_exchange.json",
        headers=headers,
        timeout=timeout,
    )
    if index_response.status_code >= 400:
        raise ClassificationSyncError(
            f"SEC company_tickers_exchange request failed ({index_response.status_code}): "
            f"{index_response.text[:200]}"
        )
    payload = index_response.json()
    fields = payload.get("fields", [])
    rows = payload.get("data", [])
    if not fields or not rows:
        raise ClassificationSyncError("SEC company_tickers_exchange payload was empty.")

    tickers = pd.DataFrame(rows, columns=fields)
    tickers["ticker"] = tickers["ticker"].map(_normalize_sec_symbol)
    tickers["cik"] = pd.to_numeric(tickers["cik"], errors="coerce").astype("Int64")

    cik_lookup: dict[str, str] = {}
    for _, row in tickers.dropna(subset=["ticker", "cik"]).iterrows():
        ticker = str(row["ticker"]).strip().upper()
        cik = f"{int(row['cik']):010d}"
        cik_lookup[ticker] = cik

    profiles: list[dict[str, Any]] = []
    last_request_ts = 0.0
    for idx, symbol in enumerate(symbols, start=1):
        cik = None
        for candidate in _sec_alt_symbols(symbol):
            cik = cik_lookup.get(_normalize_sec_symbol(candidate))
            if cik:
                break
        if not cik:
            profiles.append({"symbol": symbol})
            continue

        elapsed = time.monotonic() - last_request_ts
        wait = float(request_delay) - elapsed
        if wait > 0:
            time.sleep(wait)

        submission_url = f"https://data.sec.gov/submissions/CIK{cik}.json"
        response = session.get(submission_url, headers=headers, timeout=timeout)
        last_request_ts = time.monotonic()
        if response.status_code >= 400:
            raise ClassificationSyncError(
                f"SEC submissions request failed ({response.status_code}) for {symbol}/{cik}: "
                f"{response.text[:200]}"
            )
        payload = response.json()
        profiles.append(
            {
                "symbol": str(symbol).strip().upper(),
                "sector": _sec_owner_org_to_sector(payload.get("ownerOrg")),
                "industry": payload.get("sicDescription"),
                "sic": payload.get("sic"),
                "source": "sec",
            }
        )
        if idx % 250 == 0:
            print(f"sec_profile_progress: {idx}/{len(symbols)}")

    return pd.DataFrame(profiles)


def _fetch_symbol_changes(
    session: requests.Session,
    *,
    base_url: str,
    api_key: str,
    timeout: int,
) -> pd.DataFrame:
    try:
        payload = _request_json(
            session,
            url=f"{base_url}/symbol-change",
            params={"apikey": api_key},
            timeout=timeout,
        )
        return pd.DataFrame(_extract_rows(payload))
    except ClassificationSyncError as exc:
        if "402" in str(exc):
            print("warning: symbol_change endpoint unavailable on current FMP plan; continuing without rename history")
            return pd.DataFrame()
        raise


def _fetch_delisted_companies(
    session: requests.Session,
    *,
    base_url: str,
    api_key: str,
    timeout: int,
    max_pages: int,
    page_size: int,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for page in range(max(1, int(max_pages))):
        payload = _request_json(
            session,
            url=f"{base_url}/delisted-companies",
            params={"page": page, "limit": int(page_size), "apikey": api_key},
            timeout=timeout,
        )
        chunk = _extract_rows(payload)
        if not chunk:
            break
        rows.extend(chunk)
        if len(chunk) < int(page_size):
            break
    return pd.DataFrame(rows)


def run_classification_sync(args: argparse.Namespace) -> tuple[Path, Path, Path]:
    cfg = load_config()
    snapshot_date = parse_trade_date(args.snapshot_date)
    reference_dir = (
        Path(args.reference_dir).resolve()
        if args.reference_dir
        else cfg.reference_dir
    )
    universe_file = (
        Path(args.universe_file).resolve()
        if args.universe_file
        else (cfg.private_dir / "universe.csv")
    )
    base_url = str(
        getattr(args, "base_url", "")
        or os.getenv("FMP_API_BASE_URL", "https://financialmodelingprep.com/stable")
    ).strip().rstrip("/")
    api_key = str(os.getenv("FMP_API_KEY", "")).strip()
    sec_user_agent = str(
        os.getenv("SEC_USER_AGENT", "worldquant-alpha research contact@example.com")
    ).strip() or "worldquant-alpha research contact@example.com"

    session = requests.Session()
    timeout = max(5, int(args.timeout))
    symbols = _read_universe_symbols(universe_file)
    profile_mode = "fmp_bulk"
    profiles: pd.DataFrame | None = None
    changes = pd.DataFrame()
    delisted = pd.DataFrame()

    if api_key:
        try:
            profiles = _fetch_profile_bulk(
                session,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_parts=max(1, int(args.max_parts)),
            )
        except ClassificationSyncError as exc:
            if not any(code in str(exc) for code in ["402", "429"]):
                raise
            profiles = None
        try:
            changes = _fetch_symbol_changes(
                session,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
            )
        except ClassificationSyncError:
            changes = pd.DataFrame()
        try:
            delisted = _fetch_delisted_companies(
                session,
                base_url=base_url,
                api_key=api_key,
                timeout=timeout,
                max_pages=max(1, int(args.max_pages)),
                page_size=max(100, int(args.page_size)),
            )
        except ClassificationSyncError:
            delisted = pd.DataFrame()

    if profiles is None or profiles.empty:
        profiles = _fetch_sec_profiles(
            symbols=symbols,
            timeout=timeout,
            request_delay=max(0.11, float(args.sec_request_delay)),
            user_agent=sec_user_agent,
        )
        profile_mode = "sec_submissions"

    snapshot, symbol_master = build_classification_snapshot(
        profiles,
        changes,
        delisted,
        snapshot_date=snapshot_date,
    )
    if snapshot.empty:
        raise ClassificationSyncError("Classification snapshot is empty after normalization.")

    snapshot_path, latest_path, symbol_master_csv = write_classification_snapshot(
        snapshot,
        symbol_master,
        reference_dir=reference_dir,
        snapshot_date=snapshot_date,
    )

    print(f"snapshot_date: {snapshot_date.isoformat()}")
    print(f"profile_mode: {profile_mode}")
    print(f"profile_rows: {len(profiles)}")
    print(f"symbol_change_rows: {len(changes)}")
    print(f"delisted_rows: {len(delisted)}")
    print(f"classification_rows: {len(snapshot)}")
    print(f"symbol_master_rows: {len(symbol_master)}")
    print(f"snapshot_file: {snapshot_path}")
    print(f"latest_file: {latest_path}")
    print(f"symbol_master_file: {symbol_master_csv}")
    return snapshot_path, latest_path, symbol_master_csv


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Sync free FMP sector/industry classifications into the local Alpaca cache."
    )
    parser.add_argument(
        "--snapshot-date",
        default="",
        help="Snapshot date in YYYY-MM-DD. Defaults to today in ET.",
    )
    parser.add_argument(
        "--reference-dir",
        default="",
        help="Override reference directory (default: paper/alpaca/private/reference).",
    )
    parser.add_argument(
        "--universe-file",
        default="",
        help="Universe CSV used for per-symbol profile fallback (default: paper/alpaca/private/universe.csv).",
    )
    parser.add_argument(
        "--base-url",
        default="",
        help="Override FMP API base URL.",
    )
    parser.add_argument(
        "--max-parts",
        type=int,
        default=64,
        help="Maximum profile-bulk parts to request before stopping.",
    )
    parser.add_argument(
        "--max-pages",
        type=int,
        default=200,
        help="Maximum delisted-companies pages to request before stopping.",
    )
    parser.add_argument(
        "--page-size",
        type=int,
        default=1000,
        help="Page size for delisted-companies requests.",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        help="HTTP timeout in seconds.",
    )
    parser.add_argument(
        "--profile-workers",
        type=int,
        default=8,
        help="Worker count for per-symbol profile fallback requests.",
    )
    parser.add_argument(
        "--sec-request-delay",
        type=float,
        default=0.12,
        help="Delay in seconds between SEC submissions requests when SEC fallback is used.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    run_classification_sync(args)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
