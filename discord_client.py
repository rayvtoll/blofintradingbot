# import threading
from decouple import config
import discord
from logger import logger


GLOBAL_INDENT = config("GLOBAL_INDENT", cast=int, default=4)
USE_DISCORD = config("USE_DISCORD", cast=bool, default=False)
if USE_DISCORD:
    DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID", cast=int)
    DISCORD_PRIVATE_KEY = config("DISCORD_PRIVATE_KEY")


def post_to_discord(message: str, at_everyone: bool = False) -> None:
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
            await channel.send(f"{'@everyone\n' if at_everyone else ''}{message}")
            await client.close()
        except Exception as e:
            logger.error(f"Failed to post to Discord: {e}")
            await client.close()

    try:
        client.run(token=DISCORD_PRIVATE_KEY, log_handler=None)
    except Exception as e:
        logger.error(f"Failed to post to Discord: {e}")
