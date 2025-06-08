from asyncio import sleep
import threading
from typing import List
import ccxt.pro as ccxt
from copy import deepcopy
from datetime import datetime
from decouple import config, Csv
import requests
from coinalyze_scanner import CoinalyzeScanner
from misc import Candle, Liquidation, LiquidationSet
from logger import logger

from discord_client import USE_DISCORD

if USE_DISCORD:
    from discord_client import post_to_discord, json_dumps, USE_AT_EVERYONE

USE_AUTO_JOURNALING = config("USE_AUTO_JOURNALING", cast=bool, default=False)
logger.info(f"{USE_AUTO_JOURNALING=}")
if USE_AUTO_JOURNALING:
    JOURNAL_HOST_AND_PORT = config(
        "JOURNAL_HOST_AND_PORT", default="http://127.0.0.1:8000"
    )
    JOURNALING_API_KEY = config("JOURNALING_API_KEY")

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
        self, liquidation_set: LiquidationSet, scanner: CoinalyzeScanner
    ) -> None:
        self.exchange = ccxt.blofin(
            config={
                "apiKey": BLOFIN_API_KEY,
                "secret": BLOFIN_SECRET_KEY,
                "password": BLOFIN_PASSPHRASE,
            }
        )
        self.liquidation_set: LiquidationSet = liquidation_set
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

            position_size = round(
                usdt_size / self.last_candle.close * LEVERAGE * 1000, 1
            )
        except Exception as e:
            position_size = 0.1
            logger.error(f"Error setting position size: {e}")
        if not hasattr(self, "_position_size"):
            self._position_size = position_size
            logger.info(f"Initial {self._position_size=}")
            return
        if position_size != self._position_size:
            logger.info(f"{position_size=}")
            self._position_size = position_size

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

        # remove old liquidations if they are older than 35 minutes
        self.liquidation_set.remove_old_liquidations_if_needed()

        # loop over detected liquidations
        for liquidation in deepcopy(self.liquidation_set.liquidations):

            # if liquidation is already used for trade, skip it
            if liquidation.used_for_trade:
                continue

            # if order is created, disable the liquidations with the same direction from
            # the list and exit loop
            if await self.apply_strategy(liquidation):
                break

        # to prevent delay new orders, set the position size at the end of the loop
        await self.set_position_size()
        return 0

    async def apply_strategy(self, liquidation: Liquidation) -> int:
        """Apply the strategy for the exchange"""

        # check if reaction to liquidation is strong enough to place an order
        if liquidation.meets_criteria and (
            (
                liquidation.direction == "long"
                and self.last_candle.close > liquidation.candle.high
            )
            or (
                liquidation.direction == "short"
                and self.last_candle.close < liquidation.candle.low
            )
        ):
            self.positions = await self.exchange.fetch_positions(
                symbols=["BTC/USDT:USDT"]
            )

            for position in self.positions:
                if position.get("side") != liquidation.direction:
                    continue

                if (
                    self.scanner.now.weekday() not in TRADING_DAYS
                    and self.scanner.now.hour not in TRADING_HOURS
                ):
                    logger.info(
                        f"Outside trading hours and days, not placing another {liquidation.direction} order."
                    )
                    return 0

                if (
                    position.get("contracts") > 0.1
                ):  # TODO: this will no longer work if also implementing 1m strategy
                    logger.info(
                        f"Already in {liquidation.direction} position with {position.get('contracts')} contracts, skipping order"
                    )
                    return 0

            # outside trading hours and days, place a small order, inside trading hours
            # and days, place a full order
            amount = (
                self.position_size
                if self.scanner.now.weekday() in TRADING_DAYS
                and self.scanner.now.hour in TRADING_HOURS
                else 0.1
            )

            # place the order
            logger.info(f"Placing {liquidation.direction} order")

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

                if USE_AUTO_JOURNALING:
                    response = None
                    try:
                        data = dict(
                            start=f"{self.scanner.now}",
                            entry_price=self.last_candle.close,
                            candles_before_entry=int(
                                round(
                                    (
                                        self.scanner.now
                                        - datetime.fromtimestamp(liquidation.time)
                                    ).seconds
                                    / 300,  # 5 minutes
                                    0,
                                )
                                - 1  # number of candles before entry, not distance
                            ),
                            side=(liquidation.direction).upper(),
                            amount=amount / 1000,
                            take_profit_price=order_params["params"]["takeProfit"][
                                "triggerPrice"
                            ],
                            stop_loss_price=order_params["params"]["stopLoss"][
                                "triggerPrice"
                            ],
                            liquidation_amount=int(
                                self.liquidation_set.total_amount(liquidation.direction)
                            ),
                            nr_of_liquidations=liquidation.nr_of_liquidations,
                        )
                        response = requests.post(
                            f"{JOURNAL_HOST_AND_PORT}/api/positions/",
                            headers={"Authorization": f"Api-Key {JOURNALING_API_KEY}"},
                            data=data,
                        )
                        response.raise_for_status()
                        logger.info(f"Position journaled: {response.json()}")
                    except Exception as e:
                        logger.error(
                            f"Error journaling position 1/2: {response.content if response else 'No response'}"
                        )
                        logger.error(f"Error journaling position 2/2: {e}")

                # TODO: add take profit by limit order instead of market order
            except Exception as e:
                logger.error(f"Error placing order: {e}")
                return 1

            self.liquidation_set.mark_liquidations_as_used(liquidation.direction)
            return 1
        return 0
