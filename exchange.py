import ccxt.pro as ccxt
from coinalyze_scanner import CoinalyzeScanner
from copy import deepcopy
from decouple import config, Csv
from logger import logger
from misc import Candle, Liquidation, LiquidationSet
import requests
from typing import List, Tuple

from discord_client import USE_DISCORD


TICKER: str = "BTC/USDT:USDT"

if USE_DISCORD:
    from discord_client import (
        get_discord_table,
        USE_AT_EVERYONE,
        DISCORD_CHANNEL_TRADES_ID,
        DISCORD_CHANNEL_POSITIONS_ID,
    )

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
LEVERAGE = config("LEVERAGE", cast=int, default=12)
logger.info(f"{LEVERAGE=}")
POSITION_PERCENTAGE = config("POSITION_PERCENTAGE", cast=float, default=1)
logger.info(f"{POSITION_PERCENTAGE=}")

# live strategy
USE_LIVE_STRATEGY = config("USE_LIVE_STRATEGY", cast=bool, default=True)
logger.info(f"{USE_LIVE_STRATEGY=}")
LIVE_SL_PERCENTAGE = config("LIVE_SL_PERCENTAGE", cast=float, default=0.54)
logger.info(f"{LIVE_SL_PERCENTAGE=}")
LIVE_TP_PERCENTAGE = config("LIVE_TP_PERCENTAGE", cast=float, default=4.74)
logger.info(f"{LIVE_TP_PERCENTAGE=}")
LIVE_TRADING_DAYS = config("LIVE_TRADING_DAYS", cast=Csv(int), default="0,1,3,4,5,6")
logger.info(f"{LIVE_TRADING_DAYS=}")
LIVE_TRADING_HOURS = config(
    "LIVE_TRADING_HOURS", cast=Csv(int), default="23,0,1,2,3,4,17,18,19,20"
)
logger.info(f"{LIVE_TRADING_HOURS=}")

# reversed strategy
USE_REVERSED_STRATEGY = config("USE_REVERSED_STRATEGY", cast=bool, default=True)
logger.info(f"{USE_REVERSED_STRATEGY=}")
REVERSED_SL_PERCENTAGE = config("REVERSED_SL_PERCENTAGE", cast=float, default=0.40)
logger.info(f"{REVERSED_SL_PERCENTAGE=}")
REVERSED_TP_PERCENTAGE = config("REVERSED_TP_PERCENTAGE", cast=float, default=4.09)
logger.info(f"{REVERSED_TP_PERCENTAGE=}")
REVERSED_TRADING_DAYS = config(
    "REVERSED_TRADING_DAYS", cast=Csv(int), default="0,1,3,4,5,6"
)
logger.info(f"{REVERSED_TRADING_DAYS=}")
REVERSED_TRADING_HOURS = config(
    "REVERSED_TRADING_HOURS", cast=Csv(int), default="5,6,7,14,15,16"
)
logger.info(f"{REVERSED_TRADING_HOURS=}")

# journaling strategy
USE_JOURNALING_STRATEGY = config("USE_JOURNALING_STRATEGY", cast=bool, default=True)
logger.info(f"{USE_JOURNALING_STRATEGY=}")
JOURNALING_SL_PERCENTAGE = config("JOURNALING_SL_PERCENTAGE", cast=float, default=0.54)
logger.info(f"{JOURNALING_SL_PERCENTAGE=}")
JOURNALING_TP_PERCENTAGE = config("JOURNALING_TP_PERCENTAGE", cast=float, default=4.74)
logger.info(f"{JOURNALING_TP_PERCENTAGE=}")
JOURNALING_TRADING_DAYS = config(
    "JOURNALING_TRADING_DAYS", cast=Csv(int), default="0,1,2,3,4,5,6"
)
logger.info(f"{JOURNALING_TRADING_DAYS=}")
JOURNALING_TRADING_HOURS = config(
    "JOURNALING_TRADING_HOURS",
    cast=Csv(int),
    default="0,1,2,3,4,5,6,7,8,9,10,11,12,13,14,15,16,17,18,19,20,21,22,23",
)
logger.info(f"{JOURNALING_TRADING_HOURS=}")

# Strategy types
LIVE = "live"
REVERSED = "reversed"
JOURNALING = "journaling"

# Order Directions
LONG = "long"
SHORT = "short"


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
        self.market_tpsl_orders: List[dict] = []
        self.limit_orders: List[dict] = []
        self.scanner: CoinalyzeScanner = scanner
        self.discord_message_queue: List[Tuple[int, List[str], bool]] = []

    async def get_open_positions(self) -> List[dict]:
        """Get open positions from the exchange"""

        # get open positions info
        try:
            positions = await self.exchange.fetch_positions(symbols=[TICKER])
            open_positions = [
                {
                    "amount": f"{position.get("info", {}).get("positions")} contract(s)",
                    "direction": position.get("info", {}).get("positionSide", ""),
                    "price": f"$ {round(float(position.get("info", {}).get("averagePrice", 0.0)), 2):,}",
                    "liquidation_price": f"$ {round(float(position.get("info", {}).get("liquidationPrice", 0.0)), 2):,}",
                }
                for position in positions
            ]
        except Exception as e:
            logger.error(f"Error fetching positions: {e}")
            open_positions = []

        # get open market tpsl orders
        try:
            open_orders = await self.exchange.fetch_open_orders(params={"tpsl": True})
            market_tpsl_orders_info = [
                {
                    "amount": f"{order.get("info", {}).get("size")} contract(s)",
                    "direction": order.get("info", {}).get("positionSide", ""),
                    "stoploss": (
                        f"$ {round(float(order.get("info", {}).get("slTriggerPrice", 0.0)), 2):,}"
                        if order.get("info", {}).get("slTriggerPrice")
                        else "-"
                    ),
                    "takeprofit": (
                        f"$ {round(float(order.get("info", {}).get("tpTriggerPrice", 0.0)), 2):,}"
                        if order.get("info", {}).get("tpTriggerPrice")
                        else "-"
                    ),
                }
                for order in open_orders
            ]
        except Exception as e:
            logger.error(f"Error fetching open orders: {e}")
            market_tpsl_orders_info = []

        # get open limit orders
        try:
            open_orders = await self.exchange.fetch_open_orders()
            limit_orders_info = [
                {
                    "amount": f"{order.get("amount", 0.0)} contract(s)",
                    "orderType": order.get("info", {}).get("orderType", ""),
                    "direction": order.get("info", {}).get("side", ""),
                    "price": f"$ {round(float(order.get("info", {}).get("price", 0.0)), 2):,}",
                }
                for order in open_orders
            ]
        except Exception as e:
            logger.error(f"Error fetching open limit orders: {e}")
            limit_orders_info = []

        # only log and post to discord if there are changes
        if (
            self.positions != open_positions
            or self.market_tpsl_orders != market_tpsl_orders_info
            or self.limit_orders != limit_orders_info
        ):
            self.market_tpsl_orders = market_tpsl_orders_info
            self.limit_orders = limit_orders_info
            self.positions = open_positions
            if not any(self.market_tpsl_orders or self.limit_orders or self.positions):
                open_positions_and_orders = ["No open positions / orders."]
            else:
                open_positions_and_orders = (
                    ["Position(s):"]
                    + [get_discord_table(position) for position in self.positions]
                    + ["Market TP/SL order(s):"]
                    + [get_discord_table(order) for order in self.market_tpsl_orders]
                    + ["Limit order(s):"]
                    + [get_discord_table(order) for order in self.limit_orders]
                )
            logger.info(f"{open_positions_and_orders=}")
            if USE_DISCORD:
                self.discord_message_queue.append(
                    (DISCORD_CHANNEL_POSITIONS_ID, open_positions_and_orders, False)
                )

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
            logger.info(f"{last_candle=}")
            return last_candle
        except Exception as e:
            logger.error(f"Error fetching ohlcv: {e}")
            return None

    async def set_position_sizes(self) -> None:
        """Set the position size for the exchange"""

        try:
            # fetch balance and bid/ask
            balance: dict = await self.exchange.fetch_balance()
            total_balance: float = balance.get("USDT", {}).get("total", 1)
            _, ask = await self.get_bid_ask()

            # calculate live position size
            live_usdt_size: float = (
                total_balance / (LIVE_SL_PERCENTAGE * LEVERAGE)
            ) * POSITION_PERCENTAGE

            live_position_size: float = round(live_usdt_size / ask * LEVERAGE * 1000, 1)

            # calculate reversed position size
            reversed_usdt_size: float = (
                total_balance / (REVERSED_SL_PERCENTAGE * LEVERAGE)
            ) * POSITION_PERCENTAGE
            reversed_position_size: float = round(
                reversed_usdt_size / ask * LEVERAGE * 1000, 1
            )

            # journaling position size is a fixed small size for now
            journaling_position_size: float = 0.1

        except Exception as e:
            live_position_size = 0.1
            reversed_position_size = 0.1
            journaling_position_size = 0.1
            logger.error(f"Error setting position size: {e}")

        # set the position sizes if they are not set yet
        if (
            not hasattr(self, "_live_position_size")
            or not hasattr(self, "_reversed_position_size")
            or not hasattr(self, "_journaling_position_size")
        ):
            self._live_position_size = live_position_size
            self._reversed_position_size = reversed_position_size
            self._journaling_position_size = journaling_position_size
            logger.info(
                f"Initial {self._live_position_size=} - {self._reversed_position_size=} - {self._journaling_position_size=}"
            )
            return

        # set the position sizes if they have changed
        if (
            live_position_size != self._live_position_size
            or reversed_position_size != self._reversed_position_size
            or journaling_position_size != self._journaling_position_size
        ):
            logger.info(
                f"{live_position_size=} - {reversed_position_size=} - {journaling_position_size=}"
            )
            self._live_position_size = live_position_size
            self._reversed_position_size = reversed_position_size
            self._journaling_position_size = journaling_position_size

    @property
    def live_position_size(self) -> int:
        """Get the live position size for the exchange"""

        return self._live_position_size

    @property
    def reversed_position_size(self) -> int:
        """Get the reversed position size for the exchange"""

        return self._reversed_position_size

    @property
    def journaling_position_size(self) -> int:
        """Get the journaling position size for the exchange"""

        return self._journaling_position_size

    async def run_loop(self) -> None:
        """Run the loop for the exchange"""

        # get last bid & ask from ticker
        bid, ask = await self.get_bid_ask()

        # loop over detected liquidations
        for liquidation in self.liquidation_set.liquidations:

            bid_or_ask = bid if liquidation.direction == SHORT else ask

            # if reaction to liquidation is not strong, skip it
            if not await self.reaction_to_liquidation_is_strong(
                liquidation, bid_or_ask
            ):
                continue

            trade = False

            # if order is created exit loop
            if USE_LIVE_STRATEGY and await self.apply_live_strategy(
                liquidation, bid_or_ask
            ):
                trade = True

            if USE_REVERSED_STRATEGY and await self.apply_reversed_strategy(
                liquidation, bid_or_ask
            ):
                trade = True

            if trade:
                break

            if USE_JOURNALING_STRATEGY and await self.journaling_strategy(
                liquidation, bid_or_ask
            ):
                break

    async def reaction_to_liquidation_is_strong(
        self, liquidation: Liquidation, bid_or_ask: float
    ) -> bool:
        """Check if the reaction to the liquidation is strong enough to place an
        order"""

        if (liquidation.direction == LONG and bid_or_ask > liquidation.candle.high) or (
            liquidation.direction == SHORT and bid_or_ask < liquidation.candle.low
        ):
            return True
        return False

    async def apply_strategy(
        self,
        liquidation: Liquidation,
        bid_or_ask: float,
        days: List[int],
        hours: List[int],
        amount: float,
        strategy_type: str,
        stoploss_percentage: float,
        takeprofit_percentage: float,
    ) -> bool:
        """Apply the strategy during trading hours and days"""

        # check if we are in trading hours and days
        if self.scanner.now.weekday() not in days or self.scanner.now.hour not in hours:
            return False

        await self.market_order_placement(
            amount=amount,
            liquidation=liquidation,
            bid_or_ask=bid_or_ask,
            stoploss_percentage=stoploss_percentage,
            takeprofit_percentage=takeprofit_percentage,
            strategy_type=strategy_type,
        )
        return True

    async def journaling_strategy(
        self, liquidation: Liquidation, bid_or_ask: float
    ) -> bool:
        """Apply the journaling strategy to create datapoints for the journal with
        minimal risk"""

        return await self.apply_strategy(
            liquidation=liquidation,
            bid_or_ask=bid_or_ask,
            days=JOURNALING_TRADING_DAYS,
            hours=JOURNALING_TRADING_HOURS,
            amount=self.journaling_position_size,
            strategy_type=JOURNALING,
            stoploss_percentage=JOURNALING_SL_PERCENTAGE,
            takeprofit_percentage=JOURNALING_TP_PERCENTAGE,
        )

    async def apply_reversed_strategy(
        self, liquidation: Liquidation, bid_or_ask: float
    ) -> bool:
        """Apply the reversed strategy during trading hours and days"""

        # invert direction for reversed strategy
        reversed_liquidation = deepcopy(liquidation)
        reversed_liquidation.direction = (
            LONG if liquidation.direction == SHORT else SHORT
        )

        return await self.apply_strategy(
            liquidation=reversed_liquidation,
            bid_or_ask=bid_or_ask,
            days=REVERSED_TRADING_DAYS,
            hours=REVERSED_TRADING_HOURS,
            amount=self.reversed_position_size,
            strategy_type=REVERSED,
            stoploss_percentage=REVERSED_SL_PERCENTAGE,
            takeprofit_percentage=REVERSED_TP_PERCENTAGE,
        )

    async def apply_live_strategy(
        self, liquidation: Liquidation, bid_or_ask: float
    ) -> bool:
        """Apply the live strategy during trading hours and days"""

        return await self.apply_strategy(
            liquidation=liquidation,
            bid_or_ask=bid_or_ask,
            days=LIVE_TRADING_DAYS,
            hours=LIVE_TRADING_HOURS,
            amount=self.live_position_size,
            strategy_type=LIVE,
            stoploss_percentage=LIVE_SL_PERCENTAGE,
            takeprofit_percentage=LIVE_TP_PERCENTAGE,
        )

    async def get_sl_and_tp_price(
        self,
        liquidation: Liquidation,
        bid_or_ask: float,
        stoploss_percentage: float,
        takeprofit_percentage: float,
    ) -> tuple[float, float]:
        """Calculate stop loss and take profit prices based on the liquidation
        direction"""

        stoploss_price = (
            round(bid_or_ask * (1 - (stoploss_percentage / 100)), 1)
            if liquidation.direction == LONG
            else round(bid_or_ask * (1 + (stoploss_percentage / 100)), 1)
        )
        takeprofit_price = (
            round(bid_or_ask * (1 + (takeprofit_percentage / 100)), 1)
            if liquidation.direction == LONG
            else round(bid_or_ask * (1 - (takeprofit_percentage / 100)), 1)
        )
        return stoploss_price, takeprofit_price

    async def get_bid_ask(self) -> tuple[float, float]:
        """Get the current bid and ask prices from the exchange ticker"""

        ticker_data = await self.exchange.fetch_ticker(symbol=TICKER)
        bid, ask = ticker_data["bid"], ticker_data["ask"]
        return bid, ask

    async def market_order_placement(
        self,
        amount: float,
        liquidation: Liquidation,
        bid_or_ask: float,
        stoploss_percentage: float,
        takeprofit_percentage: float,
        strategy_type: str,
    ) -> None:
        """Process the order placement for the strategy using a market order"""

        logger.info(f"Placing {liquidation.direction} order")
        try:
            stoploss_price, takeprofit_price = await self.get_sl_and_tp_price(
                liquidation, bid_or_ask, stoploss_percentage, takeprofit_percentage
            )
            order = await self.exchange.create_order(
                symbol=TICKER,
                type="market",
                side="buy" if liquidation.direction == LONG else "sell",
                amount=amount,
                params=dict(
                    marginMode="isolated",
                    positionSide=liquidation.direction,
                    stopLoss=dict(reduceOnly=True, triggerPrice=stoploss_price),
                    takeProfit=dict(reduceOnly=True, triggerPrice=takeprofit_price),
                ),
            )
            await self.do_order_logging(
                liquidation,
                bid_or_ask,
                stoploss_price,
                takeprofit_price,
                amount,
                strategy_type,
            )
        except Exception as e:
            logger.error(f"Error placing order: {e}")

    async def do_order_logging(
        self,
        liquidation: Liquidation,
        price: float,
        stoploss_price: float,
        takeprofit_price: float,
        amount: float,
        strategy_type: str,
    ) -> None:
        """Log the order details"""

        try:
            reaction_liquidation = deepcopy(liquidation)

            # revert back the liquidation direction for logging and journaling
            if strategy_type == REVERSED:
                reaction_liquidation.direction = (
                    LONG if liquidation.direction == SHORT else SHORT
                )
            order_log_info = dict(
                strategy_type=strategy_type.capitalize(),
                trade_direction=liquidation.direction,
                amount=f"{amount} contract(s)",
                price=f"$ {round(price, 2):,}",
                stoploss=f"$ {round(stoploss_price, 2):,}",
                takeprofit=f"$ {round(takeprofit_price, 2):,}",
                reaction_to_liquidation=reaction_liquidation.to_dict(),
            )
            logger.info(f"{order_log_info=}")
            if USE_DISCORD and strategy_type != JOURNALING:
                self.discord_message_queue.append(
                    (
                        DISCORD_CHANNEL_TRADES_ID,
                        [
                            f":bar_chart: New {liquidation.direction} trade :rocket:",
                            f"{get_discord_table(order_log_info)}",
                        ],
                        True if USE_AT_EVERYONE else False,
                    )
                )

            if USE_AUTO_JOURNALING:
                response = None
                try:
                    data = dict(
                        start=f"{self.scanner.now}",
                        entry_price=price,
                        candles_before_entry=1,
                        side=(liquidation.direction).upper(),
                        amount=amount / 1000,
                        take_profit_price=takeprofit_price,
                        stop_loss_price=stoploss_price,
                        liquidation_amount=int(
                            self.liquidation_set.total_amount(
                                reaction_liquidation.direction
                            )
                        ),
                        strategy_type=strategy_type,
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
            logger.error(f"Error logging order: {e}")
