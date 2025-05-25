import threading
from decouple import config
import discord


USE_DISCORD = config("USE_DISCORD", cast=bool, default=False)
if USE_DISCORD:
    DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID", cast=int)
    DISCORD_PRIVATE_KEY = config("DISCORD_PRIVATE_KEY")


def post_to_discord(message: str, at_everyone: bool = False) -> None:
    """Post a message to discord

    Args:
        message (str): message to post to discord
    """
    if not USE_DISCORD:
        return

    def _post(message: str, at_everyone: bool = False) -> None:
        """Post a message to discord in a separate thread"""

        intents = discord.Intents.default()
        intents.messages = True
        client = discord.Client(intents=intents)

        @client.event
        async def on_ready():
            channel = client.get_channel(DISCORD_CHANNEL_ID)
            await channel.send(f"{'@everyone\n' if at_everyone else ''}{message}")
            await client.close()

        client.run(token=DISCORD_PRIVATE_KEY, log_handler=None)

    threading.Thread(target=_post, args=(message, at_everyone)).start()
