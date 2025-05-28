from asyncio import run, sleep
from coinalyze_scanner import CoinalyzeScanner, COINALYZE_LIQUIDATION_URL
from datetime import datetime
from decouple import config, Csv
from discord_client import post_to_discord
from exchange import Exchange
import json
from logger import logger
import threading


LIQUIDATIONS = []

TRADING_DAYS = config("TRADING_DAYS", cast=Csv(int), default=[])
logger.info(f"{TRADING_DAYS=}")

TRADING_HOURS = config("TRADING_HOURS", cast=Csv(int), default=[])
logger.info(f"{TRADING_HOURS=}")


async def main() -> None:
    first_run = True

    # enable scanner
    scanner = CoinalyzeScanner(datetime.now(), LIQUIDATIONS)
    await scanner.set_symbols()

    # enable exchange
    exchange = Exchange(LIQUIDATIONS, scanner)

    # clear the terminal and start the bot
    logger.info("Starting the Bot...")
    logger.info(
        "BTC markets that will be scanned: %s", ", ".join(scanner.symbols.split(","))
    )

    while True:
        now = datetime.now()
        if now.minute == 59 and now.second == 0:
            logger.info(f"{exchange.positions=}")
            threading.Thread(
                target=post_to_discord,
                args=(f"Open positions:\n{json.dumps(exchange.positions, indent=2)}",),
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
