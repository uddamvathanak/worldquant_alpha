from __future__ import annotations

from datetime import date
from typing import Any

import pandas as pd


class BrokerError(RuntimeError):
    pass


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
            "status": str(getattr(order, "status", "submitted")).lower(),
            "symbol": symbol.strip().upper(),
            "side": side_norm,
            "notional": float(notional),
        }

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

