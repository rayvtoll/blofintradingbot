from decouple import config
import discord

# discord
DISCORD_CHANNEL_ID = config("DISCORD_CHANNEL_ID", cast=int, default=0)
DISCORD_PRIVATE_KEY = config("DISCORD_PRIVATE_KEY", default="")


def post_to_discord(message: str) -> None:
    """Post a message to discord

    Args:
        message (str): message to post to discord
    """
    # setup discord
    intents = discord.Intents.default()
    intents.messages = True
    client = discord.Client(intents=intents)

    @client.event
    async def on_ready():
        channel = client.get_channel(DISCORD_CHANNEL_ID)
        await channel.send(f"@everyone\n{message}")
        await client.close()

    client.run(token=DISCORD_PRIVATE_KEY, log_handler=None)
