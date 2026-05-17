from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Optional

import pandas as pd


class VendorError(RuntimeError):
    pass


class DataVendor(ABC):
    """Vendor-agnostic interface for market and reference data.

    Concrete implementations (yfinance, Polygon, AlphaVantage, IEX) live in
    sibling modules. The default vendor is yfinance; paid vendors are wired in
    when API keys are present.
    """

    name: str = "abstract"

    @abstractmethod
    def history(self, ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        ...

    @abstractmethod
    def last_price(self, ticker: str) -> Optional[float]:
        ...

    def fundamentals(self, ticker: str) -> dict:
        return {}

    def corporate_actions(self, ticker: str) -> pd.DataFrame:
        return pd.DataFrame(columns=["date", "type", "value"])

    def is_delisted(self, ticker: str) -> bool:
        return False

    def supports_point_in_time(self) -> bool:
        return False
