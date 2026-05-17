from __future__ import annotations

import os
from typing import Optional

import pandas as pd

from .base import DataVendor, VendorError
from .registry import register_vendor


class PolygonVendor(DataVendor):
    """Stub for Polygon.io. Wire up when an API key is available.

    Set POLYGON_API_KEY in the environment, then implement the methods below
    against https://polygon.io/docs.
    """

    name = "polygon"

    def __init__(self) -> None:
        self.api_key = os.environ.get("POLYGON_API_KEY")
        if not self.api_key:
            raise VendorError("POLYGON_API_KEY not set. Polygon vendor is a stub until configured.")

    def history(self, ticker: str, period: str = "6mo", interval: str = "1d") -> pd.DataFrame:
        raise NotImplementedError("Polygon history fetch not wired yet.")

    def last_price(self, ticker: str) -> Optional[float]:
        raise NotImplementedError("Polygon last price fetch not wired yet.")

    def supports_point_in_time(self) -> bool:
        # Polygon's flat-files product DOES support PIT — flip this once wired.
        return True


register_vendor("polygon", PolygonVendor)
