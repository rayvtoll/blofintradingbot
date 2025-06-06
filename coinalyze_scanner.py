from datetime import datetime, timedelta
from decouple import config
from functools import cached_property
import json
from logger import logger
from misc import Candle, Liquidation
import requests
import threading
from typing import List

from discord_client import USE_DISCORD

if USE_DISCORD:
    from discord_client import post_to_discord, json_dumps


COINALYZE_SECRET_API_KEY = config("COINALYZE_SECRET_API_KEY")
COINALYZE_LIQUIDATION_URL = "https://api.coinalyze.net/v1/liquidation-history"
FUTURE_MARKETS_URL = "https://api.coinalyze.net/v1/future-markets"

MINIMAL_NR_OF_LIQUIDATIONS = config("MINIMAL_NR_OF_LIQUIDATIONS", default=3, cast=int)
logger.info(f"{MINIMAL_NR_OF_LIQUIDATIONS=}")

N_MINUTES_TIMEDELTA = config("N_MINUTES_TIMEDELTA", default=5, cast=int)
logger.info(f"{N_MINUTES_TIMEDELTA=}")

MINIMAL_LIQUIDATION = config("MINIMAL_LIQUIDATION", default=10_000, cast=int)
logger.info(f"{MINIMAL_LIQUIDATION=}")

INTERVAL = config("INTERVAL", default="5min")
logger.info(f"{INTERVAL=}")


class CoinalyzeScanner:
    """Scans coinalyze to notify for changes in open interest and liquidations through
    text to speech"""

    def __init__(self, now: datetime, liquidations: List[Liquidation]) -> None:
        self.now = now
        self.liquidations = liquidations

    @property
    def params(self) -> dict:
        """Returns the parameters for the request to the API"""
        return {
            "symbols": self.symbols,
            "from": int(
                datetime.timestamp(self.now - timedelta(minutes=N_MINUTES_TIMEDELTA))
            ),
            "to": int(datetime.timestamp(self.now)),
            "interval": INTERVAL,
        }

    @cached_property
    def symbols(self) -> str:
        """Returns the symbols for the request to the API"""
        return self._symbols

    async def set_symbols(self) -> None:
        """Returns the symbols for the request to the API"""
        symbols = []
        for market in await self.handle_coinalyze_url(
            url=FUTURE_MARKETS_URL, include_params=False, symbols=True
        ):
            if (symbol := market.get("symbol", "").upper()).startswith("BTCUSD"):
                symbols.append(symbol)
        self._symbols = ",".join(symbols)

    async def handle_liquidation_set(self, candle: Candle, symbols: list) -> None:
        """Handle the liquidation set and check for liquidations

        Args:
            history (dict): history of the liquidation
        """

        total_long, total_short = 0, 0
        l_time = symbols[0].get("t") if len(symbols) else 0
        nr_of_liquidations = 0
        for history in symbols:
            long = history.get("l")
            total_long += long
            if long > 100:
                nr_of_liquidations += 1
            short = history.get("s")
            total_short += short
            if short > 100:
                nr_of_liquidations += 1
        if (
            nr_of_liquidations < MINIMAL_NR_OF_LIQUIDATIONS
            and max(total_long, total_short) < 100_000
        ):
            return

        if total_long > MINIMAL_LIQUIDATION:
            liquidation = Liquidation(
                amount=total_long,
                direction="long",
                time=l_time,
                nr_of_liquidations=nr_of_liquidations,
                candle=candle,
            )
            self.liquidations.insert(0, liquidation)
        if total_short > MINIMAL_LIQUIDATION:
            liquidation = Liquidation(
                amount=total_short,
                direction="short",
                time=l_time,
                nr_of_liquidations=nr_of_liquidations,
                candle=candle,
            )
            self.liquidations.insert(0, liquidation)
        if USE_DISCORD and self.liquidations:
            threading.Thread(
                target=post_to_discord,
                kwargs=dict(
                    messages=["liquidations:"]
                    + [f"{json_dumps(liq.to_dict())}" for liq in self.liquidations]
                ),
            ).start()

    async def handle_coinalyze_url(
        self, url: str, include_params: bool = True, symbols: bool = False
    ) -> List[dict]:
        """Handle the url and check for liquidations

        Args:
            url (str): url to check for liquidations
        """
        try:
            response = requests.get(
                url,
                headers={"api_key": COINALYZE_SECRET_API_KEY},
                params=self.params if include_params else {},
            )
            response.raise_for_status()
            response_json = response.json()
            if response_json and not symbols:
                logger.info(f"COINALYZE: {response_json}")
        except Exception as e:
            logger.error(str(e))
            return []

        if not len(response_json):
            return []

        if symbols:
            return response_json

        return [
            symbol.get("history")[0]
            for symbol in response_json
            if symbol.get("history")
        ]
