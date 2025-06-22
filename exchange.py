import threading
from typing import List
import ccxt.pro as ccxt
from datetime import datetime
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

    async def get_open_positions(self) -> List[dict]:
        """Get open positions for the exchange"""

        try:
            positions: List[dict] = await self.exchange.fetch_positions(
                symbols=[TICKER]
            )
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            positions = []
        return positions

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

    async def get_last_candle(self) -> Candle | None:
        """Get the last candle for the exchange"""

        try:

            last_candles = await self.exchange.fetch_ohlcv(
                symbol=TICKER,
                timeframe="5m",
                since=None,
                limit=2,
            )
            last_candle: Candle = Candle(*last_candles[-1])
            logger.info(f"{self.last_candle=}")
            return last_candle
        except Exception as e:
            logger.error(f"Error fetching ohlcv: {e}")
            return None

    async def set_position_size(self) -> None:
        """Set the position size for the exchange"""

        try:
            balance: dict = await self.exchange.fetch_balance()
            total_balance: float = balance.get("USDT", {}).get("total", 1)
            usdt_size: float = (total_balance / (0.5 * LEVERAGE)) * POSITION_PERCENTAGE

            _, ask = await self.get_bid_ask()
            position_size: float = round(usdt_size / ask * LEVERAGE * 1000, 1)
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
        bid, ask = await self.get_bid_ask()

        # loop over detected liquidations
        for liquidation in self.liquidation_set.liquidations:

            # if reaction to liquidation is not strong, skip it
            if not await self.reaction_to_liquidation_is_strong(liquidation, bid, ask):
                continue

            # if order is created exit loop
            if await self.apply_live_strategy(liquidation, bid, ask):
                break

            if await self.journaling_strategy(liquidation, bid, ask):
                break

    async def reaction_to_liquidation_is_strong(
        self, liquidation: Liquidation, bid: float, ask: float
    ) -> bool:
        """Check if the reaction to the liquidation is strong enough to place an
        order"""

        if (liquidation.direction == "long" and bid > liquidation.candle.high) or (
            liquidation.direction == "short" and ask < liquidation.candle.low
        ):
            return True
        return False

    async def journaling_strategy(
        self, liquidation: Liquidation, bid: float, ask: float
    ) -> bool:
        """Apply the journaling strategy to create datapoints for the journal with
        minimal risk"""

        for position in self.positions:
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
        self, liquidation: Liquidation, bid: float, ask: float
    ) -> bool:
        """Apply the live strategy during trading hours and days"""

        # check if we are in trading hours and days
        if (
            self.scanner.now.weekday() not in TRADING_DAYS
            or self.scanner.now.hour not in TRADING_HOURS
        ):
            return False

        for position in self.positions:
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

    async def get_sl_and_tp(
        self, liquidation: Liquidation, bid: float, ask: float
    ) -> tuple[float, float]:
        """Calculate stop loss and take profit prices based on the liquidation
        direction"""

        stoploss_percentage = 0.005  # 0.5% stop loss
        takeprofit_percentage = 0.05  # 5% take profit

        stoploss_price = (
            round(bid * (1 - stoploss_percentage), 1)
            if liquidation.direction == "long"
            else round(ask * (1 + stoploss_percentage), 1)
        )
        takeprofit_price = (
            round(bid * (1 + takeprofit_percentage), 1)
            if liquidation.direction == "long"
            else round(ask * (1 - takeprofit_percentage), 1)
        )
        return stoploss_price, takeprofit_price

    async def get_bid_ask(self) -> tuple[float, float]:
        """Get the current bid and ask prices from the exchange ticker"""

        ticker_data = await self.exchange.fetch_ticker(symbol=TICKER)
        bid, ask = ticker_data["bid"], ticker_data["ask"]
        return bid, ask

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
            stoploss_price, takeprofit_price = await self.get_sl_and_tp(
                liquidation, bid, ask
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
                new_bid, new_ask = await self.get_bid_ask()
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
                            stoploss_price, takeprofit_price = await self.get_sl_and_tp(
                                liquidation, new_bid, new_ask
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
                        at_everyone=True if USE_AT_EVERYONE else False,
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
