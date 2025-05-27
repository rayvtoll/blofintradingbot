import threading
from typing import List
import ccxt.pro as ccxt
from copy import deepcopy
from datetime import datetime, timedelta
from decouple import config, Csv
from coinalyze_scanner import CoinalyzeScanner
from discord_client import post_to_discord
import json
from misc import Candle, Liquidation
from logger import logger


# blofin
BLOFIN_SECRET_KEY = config("BLOFIN_SECRET_KEY")
BLOFIN_API_KEY = config("BLOFIN_API_KEY")
BLOFIN_PASSPHRASE = config("BLOFIN_PASSPHRASE")

# dynamic vs fixed position size
USE_FIXED_POSITION_SIZE = config("USE_FIXED_POSITION_SIZE", cast=bool, default=False)
if USE_FIXED_POSITION_SIZE:
    FIXED_POSITION_SIZE = config("FIXED_POSITION_SIZE", cast=float, default=0.1)
    logger.info(f"{FIXED_POSITION_SIZE=}")
USE_DYNAMIC_POSITION_SIZE = not USE_FIXED_POSITION_SIZE
if USE_DYNAMIC_POSITION_SIZE:
    DYNAMIC_POSITION_PERCENTAGE = config(
        "DYNAMIC_POSITION_PERCENTAGE", cast=float, default=1.0
    )
    logger.info(f"{DYNAMIC_POSITION_PERCENTAGE=}")


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
        self.scanner: CoinalyzeScanner = scanner

    async def set_leverage(self, symbol: str, leverage: int) -> None:
        """Set the leverage for the exchange"""
        try:
            logger.info(
                await self.exchange.set_leverage(
                    symbol=symbol,
                    leverage=leverage,
                    params={"marginMode": "isolated", "positionSide": "long"},
                )
            )
            logger.info(
                await self.exchange.set_leverage(
                    symbol=symbol,
                    leverage=leverage,
                    params={"marginMode": "isolated", "positionSide": "short"},
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

    async def run_loop(self) -> int:
        """Run the loop for the exchange"""

        # check if the last candle can be set, if not, return 1
        # this is to prevent the program from crashing if the exchange is down
        if await self.set_last_candle():
            return 1

        # get open positions to prevent double positions
        self.positions = await self.exchange.fetch_positions(symbols=["BTC/USDT:USDT"])

        # loop over detected liquidations
        for liquidation in deepcopy(self.liquidations):

            # if order is created, remove the liquidations with the same direction from
            # the list and exit loop
            if await self.apply_strategy(liquidation):
                break

        # to prevent delay new orders, set the position size at the end of the loop
        await self.set_position_size()
        return 0

    async def set_position_size(self) -> None:
        """Set the position size for the exchange"""

        # fixed position size based on FIXED_POSITION_SIZE
        if USE_FIXED_POSITION_SIZE:
            self._position_size = FIXED_POSITION_SIZE
            return

        # dynamic position size based on the balance using DYNAMIC_POSITION_PERCENTAGE
        try:
            balance = await self.exchange.fetch_balance()
            free = balance.get("USDT", {}).get("total", 2)
            position_size = round(free / 100 * (DYNAMIC_POSITION_PERCENTAGE * 4), 1)
            if (
                not hasattr(self, "_position_size")
                or self._position_size != position_size
            ):
                self._position_size = position_size
                logger.info(f"{position_size=}")
        except Exception as e:
            logger.error(f"Error fetching balance {e}")
            self._position_size = 0.1

    @property
    def position_size(self) -> int:
        """Get the position size for the exchange"""
        return self._position_size

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
            for position in self.positions:
                if position.get("side") == liquidation.direction:
                    logger.info(
                        f"Already in {position.get('side')} position {position.get('info', {}).get('positionId', '')}"
                    )
                    discord_message = (
                        f"Already in {position.get('side')} position:\n{json.dumps(position, indent=2)}"
                    )
                    threading.Thread(
                        target=post_to_discord,
                        args=(discord_message,),
                    ).start()
                    return 0

            # place the order
            logger.info(f"Placing {liquidation.direction} order")

            if await self.set_last_candle():
                return 1
            try:
                order = await self.exchange.create_order(
                    symbol="BTC/USDT:USDT",
                    type="market",
                    side=("buy" if liquidation.direction == "long" else "sell"),
                    amount=self.position_size,
                    params={
                        "stopLoss": {
                            "triggerPrice": (
                                round(self.last_candle.close * 0.995, 2)
                                if liquidation.direction == "long"
                                else round(self.last_candle.close * 1.005, 1)
                            ),
                            "reduceOnly": True,
                        },
                        "takeProfit": {
                            "triggerPrice": (
                                round(self.last_candle.close * 1.015, 2)
                                if liquidation.direction == "long"
                                else round(self.last_candle.close * 0.985, 1)
                            ),
                            "reduceOnly": True,
                        },
                        "marginMode": "isolated",
                        "positionSide": liquidation.direction,
                    },
                )
                logger.info(f"{order=}")
                self.positions = await self.exchange.fetch_positions(
                    symbols=["BTC/USDT:USDT"]
                )
                logger.info(f"{self.positions=}")
                discord_message = (
                    f"{json.dumps({"order": order, "positions": self.positions}, indent=2)}"
                )
                threading.Thread(
                    target=post_to_discord,
                    args=(discord_message, True)
                ).start()
                # TODO: add take profit by limit order instead of market order0
            except Exception as e:
                logger.error(f"Error placing order: {e}")
                return 1

            # remove similar liquidations
            for liq in deepcopy(self.liquidations):
                if liq.direction == liquidation.direction:
                    self.liquidations.remove(liq)
            return 1
        return 0
