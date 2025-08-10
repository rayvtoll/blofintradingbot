from typing import List
from discord_client import USE_DISCORD, get_discord_table
from asyncio import run, sleep
from coinalyze_scanner import CoinalyzeScanner, COINALYZE_LIQUIDATION_URL
from datetime import datetime, timedelta
from exchange import Exchange, TICKER, LEVERAGE
from logger import logger
from misc import Liquidation, LiquidationSet
import threading

if USE_DISCORD:
    from coinalyze_scanner import (
        INTERVAL,
        N_MINUTES_TIMEDELTA,
    )
    from discord_client import (
        post_to_discord,
        USE_AT_EVERYONE,
        DISCORD_CHANNEL_HEARTBEAT_ID,
        DISCORD_CHANNEL_POSITIONS_ID,
    )
    from exchange import (
        POSITION_PERCENTAGE,
        USE_LIVE_STRATEGY,
        LIVE_SL_PERCENTAGE,
        LIVE_TP_PERCENTAGE,
        LIVE_TRADING_DAYS,
        LIVE_TRADING_HOURS,
        USE_GREY_STRATEGY,
        GREY_SL_PERCENTAGE,
        GREY_TP_PERCENTAGE,
        GREY_TRADING_DAYS,
        GREY_TRADING_HOURS,
        USE_JOURNALING_STRATEGY,
        JOURNALING_SL_PERCENTAGE,
        JOURNALING_TP_PERCENTAGE,
        JOURNALING_TRADING_DAYS,
        JOURNALING_TRADING_HOURS,
    )
    from misc import MINIMAL_NR_OF_LIQUIDATIONS, MINIMAL_LIQUIDATION

    DISCORD_SETTINGS = dict(
        leverage=LEVERAGE,
        position_percentage=POSITION_PERCENTAGE,
        n_minutes_timedelta=N_MINUTES_TIMEDELTA,
        minimal_nr_of_liquidations=MINIMAL_NR_OF_LIQUIDATIONS,
        minimal_liquidation=MINIMAL_LIQUIDATION,
        interval=INTERVAL,
    )
    if USE_LIVE_STRATEGY:
        DISCORD_SETTINGS["live_sl_percentage"] = LIVE_SL_PERCENTAGE
        DISCORD_SETTINGS["live_tp_percentage"] = LIVE_TP_PERCENTAGE
        DISCORD_SETTINGS["live_trading_days"] = LIVE_TRADING_DAYS
        DISCORD_SETTINGS["live_trading_hours"] = LIVE_TRADING_HOURS

    if USE_GREY_STRATEGY:
        DISCORD_SETTINGS["grey_sl_percentage"] = GREY_SL_PERCENTAGE
        DISCORD_SETTINGS["grey_tp_percentage"] = GREY_TP_PERCENTAGE
        DISCORD_SETTINGS["grey_trading_days"] = GREY_TRADING_DAYS
        DISCORD_SETTINGS["grey_trading_hours"] = GREY_TRADING_HOURS

    if USE_JOURNALING_STRATEGY:
        DISCORD_SETTINGS["journaling_sl_percentage"] = JOURNALING_SL_PERCENTAGE
        DISCORD_SETTINGS["journaling_tp_percentage"] = JOURNALING_TP_PERCENTAGE
        DISCORD_SETTINGS["journaling_trading_days"] = JOURNALING_TRADING_DAYS
        DISCORD_SETTINGS["journaling_trading_hours"] = JOURNALING_TRADING_HOURS

LIQUIDATIONS: List[Liquidation] = []
LIQUIDATION_SET: LiquidationSet = LiquidationSet(liquidations=LIQUIDATIONS)


async def main() -> None:
    first_run = True

    # enable scanner
    scanner = CoinalyzeScanner(datetime.now(), LIQUIDATION_SET)
    await scanner.set_symbols()

    # enable exchange
    exchange = Exchange(LIQUIDATION_SET, scanner)
    exchange.positions = await exchange.get_open_positions()

    for direction in ["long", "short"]:
        await exchange.set_leverage(
            symbol=TICKER,
            leverage=LEVERAGE,
            direction=direction,
        )

    # start the bot
    info = "Starting / Restarting the bot"
    logger.info(info + "...")
    logger.info(
        "BTC markets that will be scanned: %s", ", ".join(scanner.symbols.split(","))
    )
    if USE_DISCORD:
        DISCORD_SETTINGS["symbols"] = scanner.symbols.split(",")
        threading.Thread(
            target=post_to_discord,
            kwargs=dict(
                messages=[f"{info} with settings:\n{get_discord_table(DISCORD_SETTINGS)}"],
                channel_id=DISCORD_CHANNEL_HEARTBEAT_ID,
                at_everyone=True if USE_AT_EVERYONE else False,
            ),
        ).start()

    while True:
        now = datetime.now()

        if (now.minute % 5 == 0 and now.second == 0) or first_run:
            first_run = False
            scanner.now = now

            # run strategy for the exchange on LIQUIDATIONS list
            await exchange.run_loop()

            # check for fresh liquidations and add to LIQUIDATIONS list
            await scanner.handle_liquidation_set(
                await exchange.get_last_candle(),
                await scanner.handle_coinalyze_url(COINALYZE_LIQUIDATION_URL),
            )
            if LIQUIDATIONS:
                logger.info(f"{LIQUIDATIONS=}")

            await sleep(0.99)  # prevent double processing

        # send a hearbeat to discord every 4 hours
        if now.hour % 4 == 0 and now.minute == 1 and now.second == 0:
            if USE_DISCORD:
                threading.Thread(
                    target=post_to_discord,
                    kwargs=dict(
                        messages=["."],
                        channel_id=DISCORD_CHANNEL_HEARTBEAT_ID,
                    ),
                ).start()

            await sleep(0.99)  # prevent double processing

        if now.minute % 5 == 3 and now.second == 0:
            # get positions info and set exchange.positions
            try:
                positions = await exchange.exchange.fetch_positions(symbols=[TICKER])
                exchange.positions = [
                    position.get("info", {}) for position in positions
                ]
            except Exception as e:
                logger.error(f"Error fetching positions: {e}")
                exchange.positions = []

            # get open orders and compare to exchange.open_orders
            try:
                open_orders = await exchange.exchange.fetch_open_orders(
                    params={"tpsl": True}
                )
                open_orders_info = [order.get("info", {}) for order in open_orders]
            except Exception as e:
                logger.error(f"Error fetching open orders: {e}")
                open_orders_info = []

            # only log and post to discord if there are changes
            if exchange.open_orders != open_orders_info:
                exchange.open_orders = open_orders_info
                open_positions_and_orders = (
                    ["open_positions:"]
                    + [get_discord_table(position) for position in exchange.positions]
                    + ["open_orders:"]
                    + [get_discord_table(order) for order in exchange.open_orders]
                )
                logger.info(f"{open_positions_and_orders=}")
                if USE_DISCORD:
                    threading.Thread(
                        target=post_to_discord,
                        kwargs=dict(
                            messages=open_positions_and_orders,
                            channel_id=DISCORD_CHANNEL_POSITIONS_ID,
                            at_everyone=True if USE_AT_EVERYONE else False,
                        ),
                    ).start()

            await sleep(0.99)  # prevent double processing

        if now.minute % 5 == 4 and now.second == 0:
            exchange.liquidation_set.remove_old_liquidations(now + timedelta(minutes=1))
            exchange.positions = await exchange.get_open_positions()
            await exchange.set_position_size()

            await sleep(0.99)  # prevent double processing

        # sleep some just in case
        await sleep(0.01)


if __name__ == "__main__":
    run(main())
