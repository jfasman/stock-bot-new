"""Read-only Tradier client.

API ref: https://documentation.tradier.com/brokerage-api
Auth: single bearer token in the Authorization header.
Sandbox base: https://sandbox.tradier.com  (paper/sandbox account)
Production base: https://api.tradier.com    (real account)

Endpoints used:
  - GET /v1/accounts/{account_id}/balances
  - GET /v1/accounts/{account_id}/positions
  - GET /v1/accounts/{account_id}/history?start=<date>&type=trade
  - GET /v1/markets/quotes?symbols=<sym>

Tradier returns *single-row* lists wrapped in objects rather than bare lists,
e.g. ``{"positions": {"position": {...}}}`` for one position vs.
``{"positions": {"position": [{...}, {...}]}}`` for many. Helpers below
normalize that.
"""
from __future__ import annotations

import logging
from datetime import date, datetime, timezone
from typing import Any, List, Optional

from .base import AccountSummary, LivePosition, Quote, Transaction

log = logging.getLogger(__name__)


_SANDBOX_BASE = "https://sandbox.tradier.com"
_PROD_BASE = "https://api.tradier.com"


class TradierLiveBroker:
    name = "tradier"

    def __init__(
        self,
        access_token: str,
        account_id: str,
        *,
        sandbox: bool = True,
        base_url: Optional[str] = None,
        http_session=None,
        timeout: float = 10.0,
    ):
        self._token = access_token
        self._account_id = account_id
        self._base = base_url or (_SANDBOX_BASE if sandbox else _PROD_BASE)
        self._timeout = timeout
        if http_session is None:
            import requests
            http_session = requests.Session()
        self._http = http_session

    # ---- read-only protocol -----------------------------------------

    def account_summary(self) -> AccountSummary:
        data = self._get(f"{self._base}/v1/accounts/{self._account_id}/balances")
        bal = data.get("balances") or {}
        # Tradier reports buying power per account-type sub-block; pull the
        # most useful aggregate. Some accounts have "margin", "cash", "pdt".
        cash = _f(bal.get("total_cash"))
        equity = _f(bal.get("total_equity"))
        # buying_power is one level deeper for margin/cash subtypes.
        bp = (
            _f((bal.get("margin") or {}).get("stock_buying_power"))
            or _f((bal.get("cash") or {}).get("cash_available"))
            or cash
        )
        return AccountSummary(
            account_id=str(bal.get("account_number") or self._account_id),
            cash=cash or 0.0,
            buying_power=bp or 0.0,
            equity=equity or 0.0,
            last_equity=_f(bal.get("close_pl")),
            currency="USD",
            status="ACTIVE",
            pdt=_b((bal.get("pdt") or {}).get("flagged")) if isinstance(bal.get("pdt"), dict) else None,
            as_of=datetime.now(timezone.utc),
        )

    def positions(self) -> List[LivePosition]:
        data = self._get(f"{self._base}/v1/accounts/{self._account_id}/positions")
        rows = _ensure_list(_inner(data, "positions", "position"))
        out: List[LivePosition] = []
        for p in rows:
            qty = _f(p.get("quantity")) or 0.0
            avg = _f(p.get("cost_basis")) or 0.0
            # cost_basis is total, not per-share — convert.
            avg_per_share = avg / qty if qty else 0.0
            out.append(LivePosition(
                ticker=str(p.get("symbol", "")).upper(),
                quantity=qty,
                avg_entry_price=avg_per_share,
                current_price=None,             # Tradier positions endpoint omits price
                market_value=None,
                unrealized_pnl=None,
                unrealized_pnl_pct=None,
                side="long" if qty >= 0 else "short",
            ))
        return out

    def transactions(self, since: date) -> List[Transaction]:
        data = self._get(
            f"{self._base}/v1/accounts/{self._account_id}/history",
            params={"start": since.isoformat(), "type": "trade"},
        )
        rows = _ensure_list(_inner(data, "history", "event"))
        out: List[Transaction] = []
        for r in rows:
            try:
                trade = r.get("trade") or {}
                ts = _parse_date(r.get("date"))
                price = _f(trade.get("price"))
                if ts is None or price is None:
                    continue
                qty = _f(trade.get("quantity")) or 0.0
                side = "buy" if qty > 0 else "sell"
                out.append(Transaction(
                    ts=ts,
                    ticker=str(trade.get("symbol", "")).upper(),
                    side=side,
                    quantity=abs(qty),
                    price=price,
                    fees=_f(r.get("commission")) or 0.0,
                    transaction_id=str(r.get("id", "")) or None,
                    raw=r,
                ))
            except (TypeError, ValueError) as exc:
                log.warning("Skipping malformed Tradier event: %s (%s)", r, exc)
        return out

    def quote(self, symbol: str) -> Quote:
        symbol = symbol.upper()
        data = self._get(
            f"{self._base}/v1/markets/quotes",
            params={"symbols": symbol},
        )
        rows = _ensure_list(_inner(data, "quotes", "quote"))
        if not rows:
            raise RuntimeError(f"Tradier returned no quote for {symbol}")
        q = rows[0]
        return Quote(
            ticker=symbol,
            last=_f(q.get("last")) or 0.0,
            bid=_f(q.get("bid")),
            ask=_f(q.get("ask")),
            as_of=datetime.now(timezone.utc),
        )

    # ---- HTTP --------------------------------------------------------

    def _get(self, url: str, *, params: Optional[dict] = None):
        headers = {
            "Authorization": f"Bearer {self._token}",
            "Accept": "application/json",
        }
        resp = self._http.get(url, headers=headers, params=params, timeout=self._timeout)
        if resp.status_code >= 400:
            raise RuntimeError(
                f"Tradier GET {url} -> {resp.status_code}: {resp.text[:200]}"
            )
        return resp.json()


# ---- helpers ------------------------------------------------------------

def _f(v) -> Optional[float]:
    if v is None or v == "":
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _b(v) -> Optional[bool]:
    if v is None:
        return None
    if isinstance(v, bool):
        return v
    if isinstance(v, str):
        return v.lower() in ("true", "1", "yes")
    return bool(v)


def _ensure_list(v: Any) -> list:
    """Normalize Tradier's single-row-as-object quirk into a real list."""
    if v is None:
        return []
    if isinstance(v, list):
        return v
    return [v]


def _inner(data: Any, outer: str, inner: str):
    """Safely descend two levels into a nested dict.

    Tradier returns the literal string ``"null"`` for empty results
    (instead of an empty dict or null), so the usual ``(d.get(k) or {})``
    pattern fails — the truthy ``"null"`` string then can't .get().
    """
    if not isinstance(data, dict):
        return None
    outer_v = data.get(outer)
    if not isinstance(outer_v, dict):
        return None
    return outer_v.get(inner)


def _parse_date(s) -> Optional[datetime]:
    if not s:
        return None
    if isinstance(s, datetime):
        return s
    try:
        return datetime.fromisoformat(str(s).replace("Z", "+00:00"))
    except ValueError:
        try:
            # Tradier history dates are YYYY-MM-DD.
            return datetime.strptime(str(s), "%Y-%m-%d").replace(tzinfo=timezone.utc)
        except ValueError:
            return None
