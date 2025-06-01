from asyncio import sleep
import threading
from typing import List
import ccxt.pro as ccxt
from copy import deepcopy
from datetime import datetime, timedelta
from decouple import config, Csv
from coinalyze_scanner import CoinalyzeScanner
import json
from misc import Candle, Liquidation
from logger import logger

from discord_client import USE_DISCORD

if USE_DISCORD:
    from discord_client import post_to_discord, json_dumps, USE_AT_EVERYONE

# blofin
BLOFIN_SECRET_KEY = config("BLOFIN_SECRET_KEY")
BLOFIN_API_KEY = config("BLOFIN_API_KEY")
BLOFIN_PASSPHRASE = config("BLOFIN_PASSPHRASE")

# trade settings
LEVERAGE = config("LEVERAGE", cast=int, default=8)
logger.info(f"{LEVERAGE=}")
POSITION_PERCENTAGE = config("POSITION_PERCENTAGE", cast=float, default=1.5)
logger.info(f"{POSITION_PERCENTAGE=}")
TRADING_DAYS = config("TRADING_DAYS", cast=Csv(int), default=[])
logger.info(f"{TRADING_DAYS=}")
TRADING_HOURS = config("TRADING_HOURS", cast=Csv(int), default=[])
logger.info(f"{TRADING_HOURS=}")


class Exchange:
    """Exchange class to handle the exchange"""

    def __init__(
        self, liquidations: List[Liquidation], scanner: CoinalyzeScanner
    ) -> None:
        self.exchange = ccxt.blofin(
            config={
                "apiKey": BLOFIN_API_KEY,
                "secret": BLOFIN_SECRET_KEY,
                "password": BLOFIN_PASSPHRASE,
            }
        )
        self.liquidations: List[Liquidation] = liquidations
        self.positions: List[dict] = []
        self.open_orders: List[dict] = []
        self.scanner: CoinalyzeScanner = scanner

    async def set_leverage(self, symbol: str, leverage: int, direction: str) -> None:
        """Set the leverage for the exchange"""
        try:
            logger.info(
                await self.exchange.set_leverage(
                    symbol=symbol,
                    leverage=leverage,
                    params={"marginMode": "isolated", "positionSide": direction},
                )
            )
        except Exception as e:
            logger.warning(f"Error settings leverage: {e}")

    async def set_last_candle(self) -> int:
        """Set the last candle for the exchange"""
        try:

            last_candles = await self.exchange.fetch_ohlcv(
                symbol="BTC/USDT:USDT",
                timeframe="5m",
                since=None,
                limit=2,
            )
            self.last_candle = Candle(*last_candles[-1])
            logger.info(f"{self.last_candle=}")
        except Exception as e:
            logger.error(f"Error fetching ohlcv: {e}")
            return 1
        return 0

    async def set_position_size(self) -> None:
        """Set the position size for the exchange"""
        try:
            balance = await self.exchange.fetch_balance()
            total_balance = balance.get("USDT", {}).get("total", 1)
            usdt_size = (total_balance / (0.5 * LEVERAGE)) * POSITION_PERCENTAGE

            self._position_size = round(
                usdt_size / self.last_candle.close * LEVERAGE * 1000, 1
            )
        except Exception as e:
            self._position_size = 0.1
            logger.error(f"Error setting position size: {e}")
        logger.info(f"{self._position_size=}")

    @property
    def position_size(self) -> int:
        """Get the position size for the exchange"""
        return self._position_size

    async def run_loop(self) -> int:
        """Run the loop for the exchange"""

        # check if the last candle can be set, if not, return 1
        # this is to prevent the program from crashing if the exchange is down
        if await self.set_last_candle():
            return 1

        # loop over detected liquidations
        for liquidation in deepcopy(self.liquidations):

            # if order is created, remove the liquidations with the same direction from
            # the list and exit loop
            if await self.apply_strategy(liquidation):
                break

        # to prevent delay new orders, set the position size at the end of the loop
        await self.set_position_size()
        return 0

    async def apply_strategy(self, liquidation: Liquidation) -> int:
        """Apply the strategy for the exchange"""

        # remove liquidations older than 35 minutes
        if liquidation.time < (datetime.now() - timedelta(minutes=35)).timestamp():
            self.liquidations.remove(liquidation)
            return 0

        # check if reaction to liquidation is strong enough to place an order
        if (
            liquidation.direction == "long"
            and self.last_candle.close > liquidation.candle.high
        ) or (
            liquidation.direction == "short"
            and self.last_candle.close < liquidation.candle.low
        ):
            self.positions = await self.exchange.fetch_positions(
                symbols=["BTC/USDT:USDT"]
            )

            # outside trading hours and days, place a small order, inside trading hours
            # and days, place a full order unless there already is a full position,
            # then also place a small order
            amount = (
                self.position_size
                if self.scanner.now.weekday() in TRADING_DAYS
                and self.scanner.now.hour in TRADING_HOURS
                else 0.1
            )
            for position in self.positions:
                if (
                    position.get("side") == liquidation.direction
                    and position.get("contracts") > 1
                ):
                    amount = 0.1

            # place the order
            logger.info(f"Placing {liquidation.direction} order")

            if await self.set_last_candle():
                return 1
            await self.set_leverage(
                symbol="BTC/USDT:USDT",
                leverage=LEVERAGE,
                direction=liquidation.direction,
            )
            try:
                order_params = dict(
                    symbol="BTC/USDT:USDT",
                    type="market",
                    side=("buy" if liquidation.direction == "long" else "sell"),
                    amount=amount,
                    params={
                        "stopLoss": {
                            "triggerPrice": (
                                round(self.last_candle.close * 0.995, 1)
                                if liquidation.direction == "long"
                                else round(self.last_candle.close * 1.005, 1)
                            ),
                            "reduceOnly": True,
                        },
                        "takeProfit": {
                            "triggerPrice": (
                                round(self.last_candle.close * 1.015, 1)
                                if liquidation.direction == "long"
                                else round(self.last_candle.close * 0.985, 1)
                            ),
                            "reduceOnly": True,
                        },
                        "marginMode": "isolated",
                        "positionSide": liquidation.direction,
                    },
                )
                order = await self.exchange.create_order(**order_params)
                order_info = order.get("info", {})
                logger.info(f"{order_info|order_params=}")
                if USE_DISCORD:
                    threading.Thread(
                        target=post_to_discord,
                        kwargs=dict(
                            messages=[f"order:\n{json_dumps(order_info|order_params)}"],
                            at_everyone=(
                                True
                                if self.scanner.now.weekday() in TRADING_DAYS
                                and self.scanner.now.hour in TRADING_HOURS
                                and USE_AT_EVERYONE
                                else False
                            ),
                        ),
                    ).start()
                await sleep(2)
                # TODO: add take profit by limit order instead of market order
            except Exception as e:
                logger.error(f"Error placing order: {e}")
                return 1

            # remove similar liquidations
            for liq in deepcopy(self.liquidations):
                if liq.direction == liquidation.direction:
                    self.liquidations.remove(liq)
            return 1
        return 0
