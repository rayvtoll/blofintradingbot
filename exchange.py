import threading
from typing import List
import ccxt.pro as ccxt
from datetime import datetime, timedelta
from decouple import config, Csv
import requests
from coinalyze_scanner import CoinalyzeScanner
from misc import Candle, Liquidation, LiquidationSet
from logger import logger

from discord_client import USE_DISCORD


TICKER: str = "BTC/USDT:USDT"

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
MINIMAL_NR_OF_LIQUIDATIONS = config("MINIMAL_NR_OF_LIQUIDATIONS", default=3, cast=int)
logger.info(f"{MINIMAL_NR_OF_LIQUIDATIONS=}")
MINIMAL_LIQUIDATION = config("MINIMAL_LIQUIDATION", default=10_000, cast=int)
logger.info(f"{MINIMAL_LIQUIDATION=}")


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

    async def set_last_candle(self) -> None:
        """Set the last candle for the exchange"""
        try:

            last_candles = await self.exchange.fetch_ohlcv(
                symbol=TICKER,
                timeframe="5m",
                since=None,
                limit=2,
            )
            self.last_candle = Candle(*last_candles[-1])
            logger.info(f"{self.last_candle=}")
        except Exception as e:
            logger.error(f"Error fetching ohlcv: {e}")

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

    async def run_loop(self) -> None:
        """Run the loop for the exchange"""

        # get last bid & ask from ticker
        ticker_data = await self.exchange.fetch_ticker(symbol=TICKER)
        bid, ask = ticker_data["bid"], ticker_data["ask"]

        # remove old liquidations if they are older than 60 minutes
        self.liquidation_set.remove_old_liquidations(self.scanner.now)

        # loop over detected liquidations
        for liquidation in self.liquidation_set.liquidations:

            # if liquidation is already used for trade, skip it
            if liquidation.used_for_trade:
                continue

            # if liquidation is not valid, skip it
            if not await self.liquidation_is_valid(liquidation):
                continue

            # if reaction to liquidation is not strong, skip it
            if not await self.reaction_to_liquidation_is_strong(liquidation, bid, ask):
                continue

            positions = await self.exchange.fetch_positions(symbols=[TICKER])

            # if order is created exit loop
            if await self.apply_live_strategy(positions, liquidation, bid, ask):
                break

            if await self.journaling_strategy(positions, liquidation, bid, ask):
                break

        # to prevent delay new orders, set the position size and last candle at the end
        # of the loop
        await self.set_last_candle()
        await self.set_position_size()

    async def reaction_to_liquidation_is_strong(
        self, liquidation: Liquidation, bid: float, ask: float
    ) -> bool:
        """Check if the reaction to the liquidation is strong enough to place an order"""
        if (liquidation.direction == "long" and bid > liquidation.candle.high) or (
            liquidation.direction == "short" and ask < liquidation.candle.low
        ):
            return True
        return False

    async def liquidation_is_valid(self, liquidation: Liquidation) -> bool:
        """Check if the liquidation is valid for trading"""
        # currently 3 liquidations and a total of > 10k OR 1 liquidation and a total
        # of > 100k
        if (
            liquidation.nr_of_liquidations < MINIMAL_NR_OF_LIQUIDATIONS
            and liquidation.amount < 100_000
        ) or liquidation.amount < MINIMAL_LIQUIDATION:
            return False
        return True

    async def journaling_strategy(
        self, positions: List[dict], liquidation: Liquidation, bid: float, ask: float
    ) -> bool:
        """Apply the journaling strategy to create datapoints for the journal with
        minimal risk"""

        for position in positions:
            if position.get("side") == liquidation.direction:
                logger.info(
                    f"Already in {liquidation.direction} position. Skipping order for journaling strategy."
                )
                return False

        await self.process_order_placement(
            amount=0.1,  # small order for journaling
            liquidation=liquidation,
            bid=bid,
            ask=ask,
            live_strategy=False,
        )
        return True

    async def apply_live_strategy(
        self, positions: List[dict], liquidation: Liquidation, bid: float, ask: float
    ) -> bool:
        """Apply the live strategy during trading hours and days"""

        # check if we are in trading hours and days
        if (
            self.scanner.now.weekday() not in TRADING_DAYS
            or self.scanner.now.hour not in TRADING_HOURS
        ):
            return False

        # check if liquidation is too old for live strategy
        now_rounded = self.scanner.now.replace(second=0, microsecond=0)
        if liquidation.time < (now_rounded - timedelta(minutes=30)).timestamp():
            return False

        for position in positions:
            if position.get("side") != liquidation.direction:
                continue

            # check if there is another journaling position or a live strategy position
            if (
                position.get("contracts") > 0.1
            ):  # TODO: this will no longer work if also implementing 1m strategy
                logger.info(
                    f"Already in {liquidation.direction} position with {position.get('contracts')} contracts, skipping order for live strategy"
                )
                return False

        await self.process_order_placement(
            amount=self.position_size,
            liquidation=liquidation,
            bid=bid,
            ask=ask,
            live_strategy=True,
        )
        return True

    async def _place_order(
        self,
        direction: str,
        price: float,
        amount: float,
        stoploss: float,
        takeprofit: float,
    ) -> dict:
        """Create an order on the exchange with stop loss and take profit"""

        try:
            side = "sell" if direction == "short" else "buy"
            order_params = dict(
                symbol=TICKER,
                type="post_only",
                price=price,
                side=side,
                amount=amount,
                params=dict(
                    stopLoss=dict(
                        triggerPrice=stoploss,
                        reduceOnly=True,
                    ),
                    takeProfit=dict(
                        triggerPrice=takeprofit,
                        reduceOnly=True,
                    ),
                    marginMode="isolated",
                    postOnly=True,
                    positionSide=direction,
                    timeInForce="PO",  # Post Only
                ),
            )
            order = await self.exchange.create_order(**order_params)
        except Exception as e:
            logger.error(f"Error placing order: {e}")
            order = {}
        return order

    async def process_order_placement(
        self,
        amount: float,
        liquidation: Liquidation,
        bid: float,
        ask: float,
        live_strategy: bool,
    ) -> None:
        """Process the order placement for a strategy"""

        logger.info(f"Placing {liquidation.direction} order")

        try:
            amount_left = amount
            price = bid if liquidation.direction == "long" else ask
            stoploss_price = (
                round(bid * 0.995, 1)
                if liquidation.direction == "long"
                else round(ask * 1.005, 1)
            )
            takeprofit_price = (
                round(bid * 1.015, 1)
                if liquidation.direction == "long"
                else round(ask * 0.985, 1)
            )
            params = dict(
                direction=liquidation.direction,
                price=price,
                amount=amount_left,
                stoploss=stoploss_price,
                takeprofit=takeprofit_price,
            )

            order = await self._place_order(**params)

            while amount_left >= 0.1:
                ticker_data = await self.exchange.fetch_ticker(symbol=TICKER)
                new_bid, new_ask = ticker_data["bid"], ticker_data["ask"]
                if (new_bid, new_ask) != (bid, ask):
                    bid, ask = new_bid, new_ask
                    if order:

                        try:
                            await self.exchange.cancel_order(order["id"], TICKER)
                        except Exception as e:
                            logger.error(f"Error cancelling order: {e}")

                        try:
                            updated_orders = await self.exchange.fetch_closed_orders(
                                symbol=TICKER,
                                limit=5,
                            )
                        except Exception as e:
                            logger.error(f"Error fetching orders: {e}")
                            updated_orders = []

                        for updated_order in updated_orders:
                            if updated_order["id"] == order["id"]:
                                logger.info(
                                    f"Order {order['id']} was updated: {updated_order['info']}"
                                )
                                amount_left -= float(
                                    updated_order["info"]["filledSize"]
                                )
                                break

                    if amount_left >= 0.1:
                        try:
                            price = (
                                new_bid if liquidation.direction == "long" else new_ask
                            )
                            stoploss_price = (
                                round(new_bid * 0.995, 1)
                                if liquidation.direction == "long"
                                else round(new_ask * 1.005, 1)
                            )
                            takeprofit_price = (
                                round(new_bid * 1.015, 1)
                                if liquidation.direction == "long"
                                else round(new_ask * 0.985, 1)
                            )
                            params = dict(
                                direction=liquidation.direction,
                                price=price,
                                amount=amount_left,
                                stoploss=stoploss_price,
                                takeprofit=takeprofit_price,
                            )
                            order = await self._place_order(**params)
                            logger.info(
                                f"Refreshed order placed: {order.get('info', {})}"
                            )
                        except Exception as e:
                            logger.error(f"Error placing order: {e}")
                            order = None

            order_info = order.get("info", {})
            order_log_info = (
                order_info
                | params
                | dict(amount=amount, liquidation=liquidation.to_dict())
            )
            logger.info(f"{order_log_info=}")
            if USE_DISCORD:
                threading.Thread(
                    target=post_to_discord,
                    kwargs=dict(
                        messages=[f"order:\n{json_dumps(order_log_info)}"],
                        at_everyone=(
                            True if live_strategy and USE_AT_EVERYONE else False
                        ),
                    ),
                ).start()

            if USE_AUTO_JOURNALING:
                response = None
                try:
                    data = dict(
                        start=f"{self.scanner.now}",
                        entry_price=price,
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
                        take_profit_price=takeprofit_price,
                        stop_loss_price=stoploss_price,
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

        except Exception as e:
            logger.error(f"Error placing order: {e}")

        self.liquidation_set.mark_liquidations_as_used(liquidation.direction)
