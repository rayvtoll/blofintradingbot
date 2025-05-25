# BloFin scalp bot

Fully automatic trading bot for Blofin Futures using liquidations from Coinalyze as a strategy.

    python -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    python .


# .env

Have the following variables in your .env file

    COINALYZE_SECRET_API_KEY=
    
    TRADING_HOURS=
    
    # BLOFIN
    BLOFIN_API_KEY=
    BLOFIN_SECRET_KEY=
    BLOFIN_PASSPHRASE=

Outside trading_hours the bot will automatically use $0.1 trading size so you can live-test with a minimal position_size

If you want to use a discord bot you add the following variables:

    USE_DISCORD=true
    DISCORD_PRIVATE_KEY=
    DISCORD_CHANNEL_ID=

You can choose a fixed or a dynamic position size:

To use a fixed position size add the following variables

    USE_FIXED_POSITION_SIZE=true
    FIXED_POSITION_SIZE=

To use dynamic position size other than 1%

    DYNAMIC_POSITION_SIZE=