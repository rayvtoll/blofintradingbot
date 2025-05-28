from typing import List
from discord_client import USE_DISCORD
from asyncio import run, sleep
from coinalyze_scanner import CoinalyzeScanner, COINALYZE_LIQUIDATION_URL
from datetime import datetime
from decouple import config, Csv
from exchange import Exchange
import json
from logger import logger
from misc import Liquidation
import threading


TRADING_DAYS = config("TRADING_DAYS", cast=Csv(int), default=[])
logger.info(f"{TRADING_DAYS=}")

TRADING_HOURS = config("TRADING_HOURS", cast=Csv(int), default=[])
logger.info(f"{TRADING_HOURS=}")

if USE_DISCORD:
    from coinalyze_scanner import (
        INTERVAL,
        MINIMAL_LIQUIDATION,
        MINIMAL_NR_OF_LIQUIDATIONS,
        N_MINUTES_TIMEDELTA,
    )
    from discord_client import GLOBAL_INDENT, USE_DISCORD, post_to_discord
    from exchange import LEVERAGE, POSITION_PERCENTAGE

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


async def main() -> None:
    first_run = True

    # enable scanner
    scanner = CoinalyzeScanner(datetime.now(), LIQUIDATIONS)
    await scanner.set_symbols()

    # enable exchange
    exchange = Exchange(LIQUIDATIONS, scanner)

    # clear the terminal and start the bot
    info = "Starting the bot"
    logger.info(info + "...")
    logger.info(
        "BTC markets that will be scanned: %s", ", ".join(scanner.symbols.split(","))
    )
    if USE_DISCORD:
        DISCORD_SETTINGS["symbols"] = scanner.symbols.split(",")
        threading.Thread(
            target=post_to_discord,
            args=(
                f"{info} with settings: {json.dumps(DISCORD_SETTINGS, indent=GLOBAL_INDENT)}",
                True,
            ),
        ).start()

    while True:
        now = datetime.now()
        if now.minute == 59 and now.second == 0:
            logger.info(f"{exchange.positions=}")
            if USE_DISCORD:
                threading.Thread(
                    target=post_to_discord,
                    args=(
                        f"Open positions: {json.dumps(exchange.positions, indent=GLOBAL_INDENT)}",
                    ),
                ).start()

            # prevent double processing
            await sleep(0.9)

        if (
            now.weekday() in TRADING_DAYS
            and now.hour in TRADING_HOURS
            and now.minute % 5 == 0
            and now.second == 0
        ) or first_run:
            first_run = False
            scanner.now = now

            # run strategy for the exchange on LIQUIDATIONS list
            if await exchange.run_loop():
                continue

            # check for fresh liquidations and add to LIQUIDATIONS list
            await scanner.handle_liquidation_set(
                exchange.last_candle,
                await scanner.handle_coinalyze_url(COINALYZE_LIQUIDATION_URL),
            )
            logger.info(f"{LIQUIDATIONS=}")

            # prevent double processing
            await sleep(0.9)

        # sleep some just in case
        await sleep(0.1)


if __name__ == "__main__":
    run(main())
