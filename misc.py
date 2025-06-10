from dataclasses import dataclass
from datetime import datetime, timedelta
from decouple import config
from logger import logger


MINIMAL_NR_OF_LIQUIDATIONS = config("MINIMAL_NR_OF_LIQUIDATIONS", default=3, cast=int)
logger.info(f"{MINIMAL_NR_OF_LIQUIDATIONS=}")

MINIMAL_LIQUIDATION = config("MINIMAL_LIQUIDATION", default=10_000, cast=int)
logger.info(f"{MINIMAL_LIQUIDATION=}")


@dataclass
class Candle:
    """Candle class to hold the candle data"""

    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float
    time_frame: str = "5m"  # Default time frame


@dataclass
class Liquidation:
    """Liquidation class to hold the liquidation data"""

    amount: int
    direction: str
    time: int
    nr_of_liquidations: int
    candle: Candle
    time_frame: str = "5m"  # Default time frame
    used_for_trade: bool = False

    @property
    def meets_criteria(self) -> bool:
        """Check if the liquidation meets the criteria for trading."""

        if (
            self.nr_of_liquidations < MINIMAL_NR_OF_LIQUIDATIONS
            and self.amount < 100_000
        ):
            return False

        return self.amount > MINIMAL_LIQUIDATION

    def to_dict(self) -> dict:
        """Convert the Liquidation instance to a json dumpable dictionary."""

        return (
            dict(date_time=str(datetime.fromtimestamp(self.time)))
            | self.__dict__
            | dict(candle=self.candle.__dict__)
        )


@dataclass
class LiquidationSet:
    """LiquidationSet class to hold a set of liquidations"""

    liquidations: list[Liquidation]

    def total_liquidations(self, direction: str) -> int:
        """Return the total number of liquidations in the set for a given direction."""

        return sum(
            liquidation.nr_of_liquidations
            for liquidation in self.liquidations
            if liquidation.direction == direction
        )

    def total_amount(self, direction: str) -> int:
        """Return the total amount of liquidations in the set for a given direction."""

        return sum(
            liquidation.amount
            for liquidation in self.liquidations
            if liquidation.direction == direction
        )

    def to_dict(self) -> dict:
        """Convert the LiquidationSet instance to a json dumpable dictionary."""

        return dict(
            liquidations=[liquidation.to_dict() for liquidation in self.liquidations]
        )

    def remove_old_liquidations_if_needed(self) -> None:
        """Remove liquidations older than 35 minutes."""

        for liquidation in self.liquidations:
            now_rounded = datetime.now().replace(second=0, microsecond=0)
            if liquidation.time < (now_rounded - timedelta(minutes=30)).timestamp():
                self.liquidations.remove(liquidation)

    def mark_liquidations_as_used(self, direction: str) -> None:
        """Mark all liquidations as used for a given direction."""

        for liquidation in self.liquidations:
            if liquidation.direction == direction:
                liquidation.used_for_trade = True
