# BloFin scalp bot

Fully automatic trading bot for Blofin Futures using liquidations from Coinalyze as a strategy.

    python -m venv venv
    source venv/bin/activate
    pip install --upgrade pip
    pip install -r requirements.txt
    python .

Have the following variables in your .env file

    COINALYZE_SECRET_API_KEY=
    
    TRADING_HOURS=
    
    # BLOFIN
    BLOFIN_API_KEY=
    BLOFIN_SECRET_KEY=
    BLOFIN_PASSPHRASE=

    # DISCORD
    DISCORD_PRIVATE_KEY=
    DISCORD_CHANNEL_ID=