# BloFin scalp bot

Fully automatic trading bot for Blofin Futures using liquidations from Coinalyze to counter trade as a strategy.

    python -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    python .

# .env

Have the following variables in your .env file

    COINALYZE_SECRET_API_KEY=
    TRADING_DAYS=
    TRADING_HOURS=
    POSITION_PERCENTAGE=
    BLOFIN_API_KEY=
    BLOFIN_SECRET_KEY=
    BLOFIN_PASSPHRASE=

If you want to use a discord bot you add the following variables:

    USE_DISCORD=true
    DISCORD_PRIVATE_KEY=
    DISCORD_CHANNEL_ID=
