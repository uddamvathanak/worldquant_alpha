from __future__ import annotations

import os
from pathlib import Path

from dotenv import load_dotenv


def main() -> None:
    env_path = Path(__file__).with_name(".env")
    load_dotenv(dotenv_path=env_path)

    key = os.getenv("APCA_API_KEY_ID")
    secret = os.getenv("APCA_API_SECRET_KEY")

    if not key or not secret:
        raise RuntimeError(
            f"Missing APCA_API_KEY_ID/APCA_API_SECRET_KEY in {env_path}."
        )

    try:
        from alpaca.trading.client import TradingClient
    except ModuleNotFoundError as exc:
        raise RuntimeError(
            "Missing dependency `alpaca-py`. Run: pip install alpaca-py"
        ) from exc

    client = TradingClient(key, secret, paper=True)
    account = client.get_account()

    print("account_number:", account.account_number)
    print("status:", account.status)
    print("currency:", account.currency)
    print("buying_power:", account.buying_power)
    print("equity:", account.equity)


if __name__ == "__main__":
    main()
