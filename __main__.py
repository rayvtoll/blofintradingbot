from typing import List
from discord_client import USE_DISCORD
from asyncio import run, sleep
from coinalyze_scanner import CoinalyzeScanner, COINALYZE_LIQUIDATION_URL
from datetime import datetime, timedelta
from exchange import Exchange, TICKER
from logger import logger
from misc import Liquidation, LiquidationSet
import threading

if USE_DISCORD:
    from coinalyze_scanner import (
        INTERVAL,
        N_MINUTES_TIMEDELTA,
    )
    from discord_client import USE_DISCORD, post_to_discord, json_dumps, USE_AT_EVERYONE
    from exchange import (
        LEVERAGE,
        POSITION_PERCENTAGE,
        TRADING_DAYS,
        TRADING_HOURS,
        MINIMAL_NR_OF_LIQUIDATIONS,
        MINIMAL_LIQUIDATION,
    )

    DISCORD_SETTINGS = dict(
        trading_days=TRADING_DAYS,
        trading_hours=TRADING_HOURS,
        leverage=LEVERAGE,
        position_percentage=POSITION_PERCENTAGE,
        n_minutes_timedelta=N_MINUTES_TIMEDELTA,
        minimal_nr_of_liquidations=MINIMAL_NR_OF_LIQUIDATIONS,
        minimal_liquidation=MINIMAL_LIQUIDATION,
        interval=INTERVAL,
    )

LIQUIDATIONS: List[Liquidation] = []
LIQUIDATION_SET: LiquidationSet = LiquidationSet(liquidations=LIQUIDATIONS)


async def main() -> None:
    first_run = True

    # enable scanner
    scanner = CoinalyzeScanner(datetime.now(), LIQUIDATION_SET)
    await scanner.set_symbols()

    # enable exchange
    exchange = Exchange(LIQUIDATION_SET, scanner)
    for direction in ["long", "short"]:
        await exchange.set_leverage(
            symbol=TICKER,
            leverage=LEVERAGE,
            direction=direction,
        )

    # clear the terminal and start the bot
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
                messages=[f"{info} with settings:\n{json_dumps(DISCORD_SETTINGS)}"],
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
                exchange.last_candle,
                await scanner.handle_coinalyze_url(COINALYZE_LIQUIDATION_URL),
            )
            if LIQUIDATIONS:
                logger.info(f"{LIQUIDATIONS=}")

            # prevent double processing
            await sleep(0.99)

        # send a hearbeat to discord every 4 hours
        if now.hour % 4 == 0 and now.minute == 1 and now.second == 0:
            if USE_DISCORD:
                threading.Thread(
                    target=post_to_discord,
                    kwargs=dict(messages=["."]),
                ).start()

        if now.minute == 58 and now.second == 0:
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
                    + [json_dumps(position) for position in exchange.positions]
                    + ["open_orders:"]
                    + [json_dumps(order) for order in exchange.open_orders]
                )
                logger.info(f"{open_positions_and_orders=}")
                if USE_DISCORD:
                    threading.Thread(
                        target=post_to_discord,
                        kwargs=dict(messages=open_positions_and_orders),
                    ).start()

            # prevent double processing
            await sleep(0.99)

        if now.minute % 5 == 4 and now.second == 0:
            exchange.liquidation_set.remove_old_liquidations(now + timedelta(minutes=1))

        # sleep some just in case
        await sleep(0.01)


if __name__ == "__main__":
    run(main())
