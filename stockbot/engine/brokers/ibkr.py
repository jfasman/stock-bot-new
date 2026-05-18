"""Read-only Interactive Brokers Client Portal Gateway client.

API ref: https://interactivebrokers.github.io/cpwebapi/
The Client Portal Gateway is a local Java app the user runs themselves; it
proxies authenticated requests to IBKR's servers. We connect to the gateway,
not to IBKR directly, so:
  - There's no API key — auth is the cookie the gateway sets after login.
  - The base URL is typically https://localhost:5000 (configurable).
  - The gateway's certs are self-signed; in production users typically
    set verify=False, which we expose as a config option.

Endpoints used:
  - GET /v1/api/iserver/accounts                     (must call once to "select" the account)
  - GET /v1/api/portfolio/{accountId}/summary
  - GET /v1/api/portfolio/{accountId}/positions/0
  - GET /v1/api/iserver/account/{accountId}/trades   (history; defaults to last 7d)
  - GET /v1/api/iserver/marketdata/snapshot?conids=<conid>&fields=...

The trade history endpoint doesn't accept a "since" filter — we filter
client-side by the supplied ``since`` date. Anything older than the
gateway's default window simply isn't reachable without a different
endpoint (`/v1/api/pa/transactions` would be the upgrade path).
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import List, Optional

from .base import AccountSummary, LivePosition, Quote, Transaction

log = logging.getLogger(__name__)


_DEFAULT_BASE = "https://localhost:5000"


class IbkrLiveBroker:
    name = "ibkr"

    def __init__(
        self,
        account_id: str,
        *,
        base_url: Optional[str] = None,
        verify_ssl: bool = False,
        http_session=None,
        timeout: float = 10.0,
    ):
        self._account_id = account_id
        self._base = (base_url or _DEFAULT_BASE).rstrip("/")
        self._verify = verify_ssl
        self._timeout = timeout
        if http_session is None:
            import requests
            http_session = requests.Session()
        self._http = http_session
        # The IBKR gateway requires a one-time "account select" call before
        # most endpoints work. We do it lazily on first read.
        self._account_selected = False

    # ---- read-only protocol -----------------------------------------

    def account_summary(self) -> AccountSummary:
        self._select_account_if_needed()
        data = self._get(f"{self._base}/v1/api/portfolio/{self._account_id}/summary")
        # Summary is a dict of named amounts: {"netliquidation": {"amount": 12345.67}, ...}
        cash = _amt(data.get("totalcashvalue") or data.get("availablefunds"))
        equity = _amt(data.get("netliquidation"))
        bp = _amt(data.get("buyingpower") or data.get("availablefunds"))
        return AccountSummary(
            account_id=self._account_id,
            cash=cash or 0.0,
            buying_power=bp or 0.0,
            equity=equity or 0.0,
            last_equity=_amt(data.get("previousdayequitywithloanvalue")),
            currency=(data.get("currency") or {}).get("value", "USD") if isinstance(data.get("currency"), dict) else "USD",
            status="ACTIVE",
            pdt=None,
            as_of=datetime.now(timezone.utc),
        )

    def positions(self) -> List[LivePosition]:
        self._select_account_if_needed()
        # The /positions/{page} endpoint paginates; page 0 covers the first
        # ~30 positions. For home use that's almost always sufficient.
        data = self._get(f"{self._base}/v1/api/portfolio/{self._account_id}/positions/0")
        if not isinstance(data, list):
            return []
        out: List[LivePosition] = []
        for p in data:
            try:
                qty = float(p.get("position", 0))
                avg = float(p.get("avgPrice", 0.0))
                cur = _safe_float(p.get("mktPrice"))
                mv = _safe_float(p.get("mktValue"))
                upnl = _safe_float(p.get("unrealizedPnl"))
                upct = (upnl / (avg * qty)) if (upnl is not None and avg and qty) else None
                out.append(LivePosition(
                    ticker=str(p.get("contractDesc") or p.get("ticker", "")).split()[0].upper(),
                    quantity=qty,
                    avg_entry_price=avg,
                    current_price=cur,
                    market_value=mv,
                    unrealized_pnl=upnl,
                    unrealized_pnl_pct=upct,
                    side="long" if qty >= 0 else "short",
                ))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed IBKR position: %s (%s)", p, exc)
        return out

    def transactions(self, since: date) -> List[Transaction]:
        self._select_account_if_needed()
        data = self._get(f"{self._base}/v1/api/iserver/account/{self._account_id}/trades")
        if not isinstance(data, list):
            return []
        out: List[Transaction] = []
        for r in data:
            try:
                ts = _parse_ibkr_time(r.get("trade_time") or r.get("submitter") or r.get("date"))
                if ts is None or ts.date() < since:
                    continue
                price = _safe_float(r.get("price"))
                if price is None:
                    continue
                side_raw = str(r.get("side", "")).upper()
                side = "buy" if side_raw in ("B", "BUY", "BOT") else "sell"
                out.append(Transaction(
                    ts=ts,
                    ticker=str(r.get("symbol") or r.get("contract_description_1", "")).upper(),
                    side=side,
                    quantity=float(r.get("size") or r.get("quantity") or 0.0),
                    price=price,
                    fees=_safe_float(r.get("commission")) or 0.0,
                    transaction_id=str(r.get("execution_id") or r.get("order_ref") or "") or None,
                    raw=r,
                ))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed IBKR trade: %s (%s)", r, exc)
        return out

    def quote(self, symbol: str) -> Quote:
        # Snapshot quotes require a conid (numeric contract id) not a ticker —
        # users supply a symbol→conid map via the contract search endpoint.
        # For Phase A we keep this simple: try a search, take the first US
        # equity hit, then fetch the snapshot.
        self._select_account_if_needed()
        symbol = symbol.upper()
        search = self._get(
            f"{self._base}/v1/api/iserver/secdef/search",
            params={"symbol": symbol, "secType": "STK"},
        )
        if not isinstance(search, list) or not search:
            raise RuntimeError(f"IBKR returned no contract for {symbol}")
        conid = search[0].get("conid")
        if not conid:
            raise RuntimeError(f"IBKR contract search for {symbol} had no conid")
        snap = self._get(
            f"{self._base}/v1/api/iserver/marketdata/snapshot",
            params={"conids": conid, "fields": "31,84,86"},   # last, bid, ask
        )
        if not isinstance(snap, list) or not snap:
            raise RuntimeError(f"IBKR snapshot for {symbol} (conid {conid}) was empty")
        row = snap[0]
        return Quote(
            ticker=symbol,
            last=_safe_float(row.get("31")) or 0.0,
            bid=_safe_float(row.get("84")),
            ask=_safe_float(row.get("86")),
            as_of=datetime.now(timezone.utc),
        )

    # ---- HTTP --------------------------------------------------------

    def _select_account_if_needed(self):
        if self._account_selected:
            return
        # The gateway requires this call exactly once per session. We don't
        # care about its return — only that it doesn't 4xx.
        self._get(f"{self._base}/v1/api/iserver/accounts")
        self._account_selected = True

    def _get(self, url: str, *, params: Optional[dict] = None):
        headers = {"Accept": "application/json"}
        resp = self._http.get(
            url, headers=headers, params=params,
            timeout=self._timeout, verify=self._verify,
        )
        if resp.status_code >= 400:
            raise RuntimeError(
                f"IBKR GET {url} -> {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()


# ---- helpers -----------------------------------------------------------

def _amt(d) -> Optional[float]:
    """IBKR summary values come as `{"amount": <num>, ...}`."""
    if d is None:
        return None
    if isinstance(d, (int, float)):
        return float(d)
    if isinstance(d, dict):
        return _safe_float(d.get("amount"))
    return _safe_float(d)


def _safe_float(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _parse_ibkr_time(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    if isinstance(s, (int, float)):
        # IBKR sometimes returns epoch ms.
        try:
            return datetime.fromtimestamp(float(s) / 1000.0, tz=timezone.utc)
        except (OverflowError, OSError, ValueError):
            return None
    s = str(s)
    for fmt in ("%Y%m%d-%H:%M:%S", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%d %H:%M:%S"):
        try:
            return datetime.strptime(s, fmt).replace(tzinfo=timezone.utc)
        except ValueError:
            continue
    try:
        return datetime.fromisoformat(s.replace("Z", "+00:00"))
    except ValueError:
        return None
