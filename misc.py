from dataclasses import dataclass


@dataclass
class Candle:
    """Candle class to hold the candle data"""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


@dataclass
class Liquidation:
    """Liquidation class to hold the liquidation data"""

    amount: int
    direction: str
    time: int
    nr_of_liquidations: int
    candle: Candle
