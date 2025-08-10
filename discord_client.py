from typing import List
from decouple import config
import discord
from logger import logger
import yaml


USE_DISCORD = config("USE_DISCORD", cast=bool, default=False)
logger.info(f"{USE_DISCORD=}")
if USE_DISCORD:
    DISCORD_CHANNEL_POSITIONS_ID = config("DISCORD_CHANNEL_POSITIONS_ID", cast=int)
    DISCORD_CHANNEL_HEARTBEAT_ID = config("DISCORD_CHANNEL_HEARTBEAT_ID", cast=int)
    DISCORD_CHANNEL_LIQUIDATIONS_ID = config(
        "DISCORD_CHANNEL_LIQUIDATIONS_ID", cast=int
    )
    DISCORD_CHANNEL_TRADES_ID = config("DISCORD_CHANNEL_TRADES_ID", cast=int)
    DISCORD_PRIVATE_KEY = config("DISCORD_PRIVATE_KEY")
    USE_AT_EVERYONE = config("USE_AT_EVERYONE", cast=bool, default=False)


def get_discord_table(obj: dict) -> str:
    """Convert a dictionary to a discord friendly table"""

    return f"```{yaml.dump(obj, sort_keys=True, default_flow_style=False)}```"


def post_to_discord(
    messages: List[str], channel_id: int, at_everyone: bool = False
) -> None:
    """Post a message to discord

    Args:
        message (str): message to post to discord
        at_everyone (bool, optional): whether to mention @everyone. Defaults to False.
    """

    intents = discord.Intents.default()
    intents.messages = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(channel_id)
            for message in messages:
                await channel.send(
                    f"{'@everyone\n' if at_everyone else ''}{message}"
                )
        except Exception as e:
            logger.error(f"Failed to post to Discord: {e}")
        finally:
            await client.close()

    try:
        client.run(token=DISCORD_PRIVATE_KEY, log_handler=None)
    except Exception as e:
        logger.error(f"Failed to post to Discord: {e}")
