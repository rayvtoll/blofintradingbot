# import threading
from typing import List
from decouple import config
import discord
import json
from logger import logger


GLOBAL_INDENT = config("GLOBAL_INDENT", cast=int, default=4)
USE_DISCORD = config("USE_DISCORD", cast=bool, default=False)
if USE_DISCORD:
    DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID", cast=int)
    DISCORD_PRIVATE_KEY = config("DISCORD_PRIVATE_KEY")


def json_dumps(obj: dict | list) -> str:
    """Convert an object to a JSON string with indentation"""

    return json.dumps(obj, indent=GLOBAL_INDENT)


def post_to_discord(messages: List[str], at_everyone: bool = False) -> None:
    """Post a message to discord

    Args:
        message (str): message to post to discord
        at_everyone (bool, optional): whether to mention @everyone. Defaults to False.
    """
    if not USE_DISCORD:
        return

    intents = discord.Intents.default()
    intents.messages = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        try:
            channel = client.get_channel(DISCORD_CHANNEL_ID)
            for message in messages:
                await channel.send(f"{'@everyone\n' if at_everyone else ''}{message}")
        except Exception as e:
            logger.error(f"Failed to post to Discord: {e}")
        finally:
            await client.close()

    try:
        client.run(token=DISCORD_PRIVATE_KEY, log_handler=None)
    except Exception as e:
        logger.error(f"Failed to post to Discord: {e}")
