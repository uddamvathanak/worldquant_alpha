from __future__ import annotations

from datetime import date
import os
from typing import Any

import pandas as pd
import requests


class BrokerError(RuntimeError):
    pass


def _normalize_base_url(raw: str, *, strip_v2_suffix: bool = False) -> str:
    base = str(raw).strip().rstrip("/")
    if strip_v2_suffix and base.lower().endswith("/v2"):
        base = base[:-3]
    return base


def _normalize_order_status(raw: object) -> str:
    text = str(raw).strip().lower()
    if "." in text:
        text = text.split(".")[-1]
    return text


def _import_alpaca() -> dict[str, Any]:
    try:
        from alpaca.trading.client import TradingClient
        from alpaca.trading.enums import OrderSide, TimeInForce
        from alpaca.trading.requests import GetCalendarRequest, MarketOrderRequest
    except ModuleNotFoundError as exc:  # pragma: no cover - environment dependent
        raise BrokerError(
            "Missing dependency `alpaca-py`. Install with: pip install alpaca-py"
        ) from exc
    return {
        "TradingClient": TradingClient,
        "OrderSide": OrderSide,
        "TimeInForce": TimeInForce,
        "MarketOrderRequest": MarketOrderRequest,
        "GetCalendarRequest": GetCalendarRequest,
    }


class AlpacaBroker:
    def __init__(self, api_key: str, api_secret: str, *, paper: bool = True) -> None:
        imported = _import_alpaca()
        self._order_side = imported["OrderSide"]
        self._tif = imported["TimeInForce"]
        self._market_order_request = imported["MarketOrderRequest"]
        self._calendar_request = imported["GetCalendarRequest"]
        trading_client = imported["TradingClient"]
        self.client = trading_client(api_key, api_secret, paper=paper)
        self.api_key = api_key
        self.api_secret = api_secret
        default_trading_base = (
            "https://paper-api.alpaca.markets" if paper else "https://api.alpaca.markets"
        )
        self.trading_api_base_url = _normalize_base_url(
            os.getenv(
                "APCA_API_BASE_URL",
                default_trading_base,
            ),
            strip_v2_suffix=True,
        )
        self.data_api_base_url = _normalize_base_url(
            os.getenv("APCA_DATA_API_BASE_URL", "https://data.alpaca.markets"),
            strip_v2_suffix=True,
        )

    def list_assets(
        self,
        *,
        status: str = "active",
        asset_class: str = "us_equity",
    ) -> pd.DataFrame:
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }
        params = {
            "status": status,
            "asset_class": asset_class,
        }
        try:
            response = requests.get(
                f"{self.trading_api_base_url}/v2/assets",
                params=params,
                headers=headers,
                timeout=20,
            )
            response.raise_for_status()
            payload = response.json()
        except Exception as exc:  # pragma: no cover - network dependent
            raise BrokerError(f"Failed to fetch assets: {exc}") from exc

        if not isinstance(payload, list):
            raise BrokerError("Unexpected /v2/assets response payload type.")

        rows: list[dict[str, Any]] = []
        for item in payload:
            if not isinstance(item, dict):
                continue
            rows.append(
                {
                    "symbol": str(item.get("symbol", "")).strip().upper(),
                    "status": str(item.get("status", "")).strip().lower(),
                    "asset_class": str(
                        item.get("class", item.get("asset_class", ""))
                    )
                    .strip()
                    .lower(),
                    "exchange": str(item.get("exchange", "")).strip().upper(),
                    "tradable": bool(item.get("tradable", False)),
                    "shortable": bool(item.get("shortable", False)),
                    "easy_to_borrow": bool(item.get("easy_to_borrow", False)),
                    "marginable": bool(item.get("marginable", False)),
                    "fractionable": bool(item.get("fractionable", False)),
                }
            )

        if not rows:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "status",
                    "asset_class",
                    "exchange",
                    "tradable",
                    "shortable",
                    "easy_to_borrow",
                    "marginable",
                    "fractionable",
                ]
            )
        return pd.DataFrame(rows)

    def get_account_snapshot(self) -> dict[str, Any]:
        account = self.client.get_account()
        return {
            "account_id": str(getattr(account, "id", "")),
            "account_number": str(getattr(account, "account_number", "")),
            "status": str(getattr(account, "status", "")),
            "currency": str(getattr(account, "currency", "")),
            "equity": float(getattr(account, "equity", 0.0)),
            "cash": float(getattr(account, "cash", 0.0)),
            "buying_power": float(getattr(account, "buying_power", 0.0)),
        }

    def list_positions(self) -> pd.DataFrame:
        positions = self.client.get_all_positions()
        rows: list[dict[str, Any]] = []
        for pos in positions:
            side = str(getattr(pos, "side", "")).lower()
            market_value = float(getattr(pos, "market_value", 0.0))
            signed_market_value = market_value
            if "short" in side:
                signed_market_value = -abs(market_value)
            rows.append(
                {
                    "symbol": str(getattr(pos, "symbol", "")).strip().upper(),
                    "qty": float(getattr(pos, "qty", 0.0)),
                    "side": side,
                    "market_value": market_value,
                    "signed_market_value": signed_market_value,
                    "avg_entry_price": float(getattr(pos, "avg_entry_price", 0.0)),
                    "unrealized_pl": float(getattr(pos, "unrealized_pl", 0.0)),
                }
            )
        if not rows:
            return pd.DataFrame(
                columns=[
                    "symbol",
                    "qty",
                    "side",
                    "market_value",
                    "signed_market_value",
                    "avg_entry_price",
                    "unrealized_pl",
                ]
            )
        return pd.DataFrame(rows)

    def get_shortable_map(self, symbols: list[str]) -> dict[str, bool]:
        out: dict[str, bool] = {}
        for symbol in sorted(set(symbols)):
            try:
                asset = self.client.get_asset(symbol)
                is_shortable = bool(getattr(asset, "shortable", False))
                is_tradable = bool(getattr(asset, "tradable", True))
                out[symbol] = bool(is_shortable and is_tradable)
            except Exception:  # pragma: no cover - network dependent
                out[symbol] = False
        return out

    def submit_market_notional_order(
        self,
        *,
        symbol: str,
        side: str,
        notional: float,
        client_order_id: str,
    ) -> dict[str, Any]:
        if notional <= 0:
            raise BrokerError("Order notional must be positive.")

        side_norm = side.strip().lower()
        if side_norm not in {"buy", "sell"}:
            raise BrokerError(f"Invalid side: {side}")

        request = self._market_order_request(
            symbol=symbol.strip().upper(),
            notional=round(float(notional), 2),
            side=self._order_side.BUY if side_norm == "buy" else self._order_side.SELL,
            time_in_force=self._tif.DAY,
            client_order_id=client_order_id,
        )
        try:
            order = self.client.submit_order(order_data=request)
        except Exception as exc:  # pragma: no cover - network dependent
            raise BrokerError(str(exc)) from exc

        return {
            "order_id": str(getattr(order, "id", "")),
            "status": _normalize_order_status(getattr(order, "status", "submitted")),
            "symbol": symbol.strip().upper(),
            "side": side_norm,
            "notional": float(notional),
        }

    def submit_market_qty_order(
        self,
        *,
        symbol: str,
        side: str,
        qty: int,
        client_order_id: str,
    ) -> dict[str, Any]:
        if qty <= 0:
            raise BrokerError("Order qty must be positive.")

        side_norm = side.strip().lower()
        if side_norm not in {"buy", "sell"}:
            raise BrokerError(f"Invalid side: {side}")

        request = self._market_order_request(
            symbol=symbol.strip().upper(),
            qty=int(qty),
            side=self._order_side.BUY if side_norm == "buy" else self._order_side.SELL,
            time_in_force=self._tif.DAY,
            client_order_id=client_order_id,
        )
        try:
            order = self.client.submit_order(order_data=request)
        except Exception as exc:  # pragma: no cover - network dependent
            raise BrokerError(str(exc)) from exc

        return {
            "order_id": str(getattr(order, "id", "")),
            "status": _normalize_order_status(getattr(order, "status", "submitted")),
            "symbol": symbol.strip().upper(),
            "side": side_norm,
            "qty": int(qty),
        }

    def get_latest_price_map(self, symbols: list[str]) -> dict[str, float]:
        symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
        symbols = sorted(set(symbols))
        if not symbols:
            return {}

        out: dict[str, float] = {}
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }
        chunk_size = 200
        for idx in range(0, len(symbols), chunk_size):
            chunk = symbols[idx : idx + chunk_size]
            try:
                response = requests.get(
                    f"{self.data_api_base_url}/v2/stocks/snapshots",
                    params={"symbols": ",".join(chunk)},
                    headers=headers,
                    timeout=15,
                )
                response.raise_for_status()
                payload = response.json()
            except Exception:  # pragma: no cover - network dependent
                continue

            snapshots: dict[str, Any] = {}
            if isinstance(payload, dict):
                if isinstance(payload.get("snapshots"), dict):
                    snapshots = payload.get("snapshots", {})
                else:
                    # Alpaca may return symbol -> snapshot directly.
                    snapshots = payload
            for symbol in chunk:
                snap = snapshots.get(symbol) or {}
                price: float | None = None

                latest_trade = snap.get("latestTrade") or {}
                if isinstance(latest_trade, dict):
                    p = latest_trade.get("p")
                    if p is not None:
                        try:
                            p_float = float(p)
                            if p_float > 0:
                                price = p_float
                        except (TypeError, ValueError):
                            pass

                if price is None:
                    for bar_key in ["minuteBar", "dailyBar", "prevDailyBar"]:
                        bar = snap.get(bar_key) or {}
                        if not isinstance(bar, dict):
                            continue
                        for field in ["c", "o"]:
                            raw = bar.get(field)
                            if raw is None:
                                continue
                            try:
                                p_float = float(raw)
                            except (TypeError, ValueError):
                                continue
                            if p_float > 0:
                                price = p_float
                                break
                        if price is not None:
                            break

                if price is not None:
                    out[symbol] = price
        return out

    def get_daily_bars(
        self,
        symbols: list[str],
        *,
        start: date,
        end: date,
        timeframe: str = "1Day",
        adjustment: str = "raw",
        limit: int = 1000,
    ) -> pd.DataFrame:
        symbols = [str(s).strip().upper() for s in symbols if str(s).strip()]
        symbols = sorted(set(symbols))
        if not symbols:
            return pd.DataFrame(
                columns=["symbol", "t", "o", "h", "l", "c", "v", "vw", "n"]
            )

        feed = os.getenv("APCA_DATA_FEED", "iex").strip() or "iex"
        headers = {
            "APCA-API-KEY-ID": self.api_key,
            "APCA-API-SECRET-KEY": self.api_secret,
        }
        rows: list[dict[str, Any]] = []
        chunk_size = 200

        for idx in range(0, len(symbols), chunk_size):
            chunk = symbols[idx : idx + chunk_size]
            page_token = ""

            while True:
                params: dict[str, Any] = {
                    "symbols": ",".join(chunk),
                    "timeframe": timeframe,
                    "start": start.isoformat(),
                    "end": end.isoformat(),
                    "adjustment": adjustment,
                    "limit": int(limit),
                    "feed": feed,
                }
                if page_token:
                    params["page_token"] = page_token

                try:
                    response = requests.get(
                        f"{self.data_api_base_url}/v2/stocks/bars",
                        params=params,
                        headers=headers,
                        timeout=20,
                    )
                    response.raise_for_status()
                    payload = response.json()
                except Exception:  # pragma: no cover - network dependent
                    break

                bars_by_symbol = payload.get("bars", {})
                if isinstance(bars_by_symbol, dict):
                    for symbol, bars in bars_by_symbol.items():
                        if not isinstance(bars, list):
                            continue
                        for bar in bars:
                            if not isinstance(bar, dict):
                                continue
                            rows.append(
                                {
                                    "symbol": str(symbol).strip().upper(),
                                    "t": bar.get("t"),
                                    "o": bar.get("o"),
                                    "h": bar.get("h"),
                                    "l": bar.get("l"),
                                    "c": bar.get("c"),
                                    "v": bar.get("v"),
                                    "vw": bar.get("vw"),
                                    "n": bar.get("n"),
                                }
                            )

                next_token = payload.get("next_page_token")
                if not next_token:
                    break
                page_token = str(next_token)

        if not rows:
            return pd.DataFrame(
                columns=["symbol", "t", "o", "h", "l", "c", "v", "vw", "n"]
            )
        out = pd.DataFrame(rows).drop_duplicates(subset=["symbol", "t"])
        return out.reset_index(drop=True)

    def list_trading_days(self, start: date, end: date) -> list[date]:
        if end < start:
            return []
        request = self._calendar_request(
            start=start.isoformat(),
            end=end.isoformat(),
        )
        calendars = self.client.get_calendar(filters=request)
        out: list[date] = []
        for item in calendars:
            raw = getattr(item, "date", None)
            if raw is None:
                continue
            if hasattr(raw, "date"):
                out.append(raw.date())
            else:
                out.append(pd.Timestamp(raw).date())
        return sorted(set(out))

    def cancel_all_orders(self) -> None:
        try:
            self.client.cancel_orders()
        except Exception as exc:  # pragma: no cover - network dependent
            raise BrokerError(str(exc)) from exc

    def close_all_positions(self, *, cancel_orders: bool = True) -> list[dict[str, Any]]:
        try:
            responses = self.client.close_all_positions(cancel_orders=cancel_orders)
        except Exception as exc:  # pragma: no cover - network dependent
            raise BrokerError(str(exc)) from exc

        out: list[dict[str, Any]] = []
        for item in responses or []:
            symbol = str(getattr(item, "symbol", "")).strip().upper()
            status = _normalize_order_status(getattr(item, "status", "submitted"))
            order_id = str(
                getattr(item, "order_id", "") or getattr(item, "id", "")
            )
            side = str(getattr(item, "side", "")).strip().lower()
            qty_raw = getattr(item, "qty", None)
            notional_raw = getattr(item, "notional", None)

            qty = 0.0
            if qty_raw not in (None, ""):
                try:
                    qty = float(qty_raw)
                except (TypeError, ValueError):
                    qty = 0.0

            notional = 0.0
            if notional_raw not in (None, ""):
                try:
                    notional = float(notional_raw)
                except (TypeError, ValueError):
                    notional = 0.0

            out.append(
                {
                    "symbol": symbol,
                    "status": status,
                    "order_id": order_id,
                    "order_side": side,
                    "order_qty": qty,
                    "order_notional": notional,
                }
            )
        return out
