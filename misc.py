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

    def to_dict(self) -> dict:
        """Convert the Liquidation instance to a json dumpable dictionary."""

        return self.__dict__ | dict(candle=self.candle.__dict__)
