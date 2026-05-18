"""Read-only Alpaca client.

API ref: https://alpaca.markets/docs/api-references/
Auth: two headers (APCA-API-KEY-ID + APCA-API-SECRET-KEY).
Endpoints used:
  - GET /v2/account
  - GET /v2/positions
  - GET /v2/account/activities/FILL?after=<iso>
  - GET /v2/stocks/{symbol}/quotes/latest

No order endpoints are imported here — the read-only guarantee is
expressed by the *absence* of methods, not by a runtime flag.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import List, Optional

from .base import AccountSummary, LivePosition, Quote, Transaction

log = logging.getLogger(__name__)


_DEFAULT_LIVE_BASE = "https://api.alpaca.markets"
_DEFAULT_PAPER_BASE = "https://paper-api.alpaca.markets"
_DEFAULT_DATA_BASE = "https://data.alpaca.markets"


class AlpacaLiveBroker:
    """Read-only LiveBroker against the Alpaca REST API."""

    name = "alpaca"

    def __init__(
        self,
        access_token: str,
        secret_key: str,
        *,
        paper: bool = True,
        base_url: Optional[str] = None,
        data_base_url: Optional[str] = None,
        http_session=None,
        timeout: float = 10.0,
    ):
        self._key_id = access_token
        self._secret = secret_key
        self._base = base_url or (_DEFAULT_PAPER_BASE if paper else _DEFAULT_LIVE_BASE)
        self._data_base = data_base_url or _DEFAULT_DATA_BASE
        self._timeout = timeout
        if http_session is None:
            import requests
            http_session = requests.Session()
        self._http = http_session

    # ---- read-only protocol --------------------------------------------

    def account_summary(self) -> AccountSummary:
        data = self._get(f"{self._base}/v2/account")
        return AccountSummary(
            account_id=str(data.get("account_number") or data.get("id") or ""),
            cash=float(data.get("cash", 0.0)),
            buying_power=float(data.get("buying_power", 0.0)),
            equity=float(data.get("equity", 0.0)),
            last_equity=_safe_float(data.get("last_equity")),
            currency=str(data.get("currency", "USD")),
            status=str(data.get("status", "ACTIVE")),
            pdt=_safe_bool(data.get("pattern_day_trader")),
            as_of=datetime.now(timezone.utc),
        )

    def positions(self) -> List[LivePosition]:
        data = self._get(f"{self._base}/v2/positions")
        out: List[LivePosition] = []
        for p in data:
            qty = float(p.get("qty", 0.0))
            avg = float(p.get("avg_entry_price", 0.0))
            cur = _safe_float(p.get("current_price"))
            mv = _safe_float(p.get("market_value"))
            upnl = _safe_float(p.get("unrealized_pl"))
            upct = _safe_float(p.get("unrealized_plpc"))
            out.append(LivePosition(
                ticker=str(p.get("symbol", "")).upper(),
                quantity=qty,
                avg_entry_price=avg,
                current_price=cur,
                market_value=mv,
                unrealized_pnl=upnl,
                unrealized_pnl_pct=upct,
                side=str(p.get("side", "long")),
            ))
        return out

    def transactions(self, since: date) -> List[Transaction]:
        # `activity_type=FILL` returns one row per fill.
        after = datetime(since.year, since.month, since.day, tzinfo=timezone.utc).isoformat()
        data = self._get(
            f"{self._base}/v2/account/activities/FILL",
            params={"after": after},
        )
        out: List[Transaction] = []
        for r in data:
            try:
                ts = _parse_iso(r.get("transaction_time"))
                price = _safe_float(r.get("price"))
                if ts is None or price is None:
                    log.warning("Skipping Alpaca activity row missing ts or price: %s", r)
                    continue
                out.append(Transaction(
                    ts=ts,
                    ticker=str(r.get("symbol", "")).upper(),
                    side=str(r.get("side", "")).lower(),
                    quantity=float(r.get("qty", 0.0)),
                    price=price,
                    transaction_id=str(r.get("id", "")) or None,
                    raw=r,
                ))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed Alpaca activity row: %s (%s)", r, exc)
        return out

    def quote(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        data = self._get(f"{self._data_base}/v2/stocks/{symbol}/quotes/latest")
        q = data.get("quote") or {}
        bid = _safe_float(q.get("bp"))
        ask = _safe_float(q.get("ap"))
        last = (bid + ask) / 2.0 if (bid is not None and ask is not None) else (bid or ask or 0.0)
        return Quote(
            ticker=symbol,
            last=float(last),
            bid=bid,
            ask=ask,
            as_of=_parse_iso(q.get("t")),
        )

    # ---- HTTP ---------------------------------------------------------

    def _get(self, url: str, *, params: Optional[dict] = None):
        headers = {
            "APCA-API-KEY-ID": self._key_id,
            "APCA-API-SECRET-KEY": self._secret,
            "Accept": "application/json",
        }
        resp = self._http.get(url, headers=headers, params=params, timeout=self._timeout)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Alpaca GET {url} -> {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()


# ---- helpers ------------------------------------------------------------

def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _safe_bool(v) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def _parse_iso(s) -> Optional[datetime]:
    if not s or not isinstance(s, str):
        return None
    # Alpaca uses 'Z' for UTC; datetime.fromisoformat doesn't accept it pre-3.11
    # in some setups, so normalize.
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
