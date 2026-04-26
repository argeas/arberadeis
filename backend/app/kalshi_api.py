"""Kalshi API client — market discovery, orderbook, and order placement.

Auth: RSA-PSS signed requests with API key + PEM private key.
Base URL: https://api.elections.kalshi.com/trade-api/v2
"""

import base64
import time
import logging
import httpx
from pathlib import Path
from app.config import config
from app.models import MarketSide

logger = logging.getLogger("arber")

BASE_URL = "https://api.elections.kalshi.com/trade-api/v2"
DEMO_URL = "https://demo-api.kalshi.co/trade-api/v2"

_private_key = None


def _load_private_key():
    """Load RSA private key from PEM file or string."""
    global _private_key
    if _private_key is not None:
        return _private_key

    if not config.kalshi_api_secret:
        return None

    try:
        from cryptography.hazmat.primitives import serialization

        # kalshi_api_secret can be a file path or PEM string
        pem_data = config.kalshi_api_secret
        if Path(pem_data).exists():
            pem_data = Path(pem_data).read_text()

        _private_key = serialization.load_pem_private_key(
            pem_data.encode() if isinstance(pem_data, str) else pem_data,
            password=None,
        )
        return _private_key
    except ImportError:
        logger.error("[KALSHI] cryptography library not installed")
    except Exception as e:
        logger.error(f"[KALSHI] Key load failed: {e}")
    return None


def _sign(timestamp_ms: str, method: str, path: str) -> str:
    """RSA-PSS sign a request."""
    key = _load_private_key()
    if not key:
        return ""
    try:
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding

        message = f"{timestamp_ms}{method}{path}".encode("utf-8")
        signature = key.sign(
            message,
            padding.PSS(
                mgf=padding.MGF1(hashes.SHA256()),
                salt_length=padding.PSS.DIGEST_LENGTH,
            ),
            hashes.SHA256(),
        )
        return base64.b64encode(signature).decode("utf-8")
    except Exception as e:
        logger.error(f"[KALSHI] Sign failed: {e}")
        return ""


def _headers(method: str, path: str) -> dict:
    """Generate authenticated headers for a Kalshi request."""
    timestamp = str(int(time.time() * 1000))
    signature = _sign(timestamp, method, path)
    return {
        "KALSHI-ACCESS-KEY": config.kalshi_api_key,
        "KALSHI-ACCESS-SIGNATURE": signature,
        "KALSHI-ACCESS-TIMESTAMP": timestamp,
        "Content-Type": "application/json",
    }


async def fetch_markets(status: str = "open", limit: int = 100) -> list[dict]:
    """Fetch active markets from Kalshi."""
    markets = []
    cursor = None
    async with httpx.AsyncClient(timeout=15) as client:
        while True:
            params = {"limit": limit, "status": status}
            if cursor:
                params["cursor"] = cursor
            path = "/markets"
            try:
                resp = await client.get(
                    f"{BASE_URL}{path}",
                    params=params,
                    headers=_headers("GET", path),
                )
                if resp.status_code != 200:
                    logger.warning(f"[KALSHI] Fetch markets: {resp.status_code}")
                    break
                data = resp.json()
                batch = data.get("markets", [])
                markets.extend(batch)
                cursor = data.get("cursor")
                if not cursor or len(batch) < limit:
                    break
            except Exception as e:
                logger.warning(f"[KALSHI] Fetch error: {e}")
                break
    return markets


async def fetch_events(status: str = "open", limit: int = 100) -> list[dict]:
    """Fetch events from Kalshi."""
    async with httpx.AsyncClient(timeout=15) as client:
        path = "/events"
        try:
            resp = await client.get(
                f"{BASE_URL}{path}",
                params={"limit": limit, "status": status},
                headers=_headers("GET", path),
            )
            if resp.status_code == 200:
                return resp.json().get("events", [])
        except Exception as e:
            logger.warning(f"[KALSHI] Events error: {e}")
    return []


async def get_orderbook(ticker: str) -> dict:
    """Get orderbook for a Kalshi market ticker."""
    async with httpx.AsyncClient(timeout=5) as client:
        path = f"/markets/{ticker}/orderbook"
        try:
            resp = await client.get(
                f"{BASE_URL}{path}",
                headers=_headers("GET", path),
            )
            if resp.status_code == 200:
                return resp.json().get("orderbook_fp", resp.json())
        except Exception as e:
            logger.warning(f"[KALSHI] Orderbook error: {e}")
    return {}


async def get_balance() -> float:
    """Get account balance in USD."""
    async with httpx.AsyncClient(timeout=10) as client:
        path = "/portfolio/balance"
        try:
            resp = await client.get(
                f"{BASE_URL}{path}",
                headers=_headers("GET", path),
            )
            if resp.status_code == 200:
                data = resp.json()
                return float(data.get("balance_dollars", 0) or data.get("balance", 0))
        except Exception as e:
            logger.warning(f"[KALSHI] Balance error: {e}")
    return 0.0


def parse_market_sides(market: dict) -> tuple[MarketSide | None, MarketSide | None]:
    """Parse a Kalshi market into YES/NO MarketSide objects."""
    try:
        ticker = market.get("ticker", "")
        title = market.get("title", "")

        # Kalshi prices as dollar strings
        yes_bid = float(market.get("yes_bid_dollars", 0) or 0)
        yes_ask = float(market.get("yes_ask_dollars", 0) or 0)
        no_bid = float(market.get("no_bid_dollars", 0) or 0)
        no_ask = float(market.get("no_ask_dollars", 0) or 0)

        # If no ask, derive from bid
        if yes_ask == 0 and yes_bid > 0:
            yes_ask = yes_bid + 0.01
        if no_ask == 0 and no_bid > 0:
            no_ask = no_bid + 0.01

        # Fallback: derive from each other
        if yes_ask > 0 and no_ask == 0:
            no_ask = 1.0 - yes_bid if yes_bid > 0 else 1.0 - yes_ask + 0.02
        if no_ask > 0 and yes_ask == 0:
            yes_ask = 1.0 - no_bid if no_bid > 0 else 1.0 - no_ask + 0.02

        if ticker and (yes_ask > 0 or no_ask > 0):
            yes_side = MarketSide(
                venue="kalshi",
                market_id=ticker,
                token_id=ticker + "_yes",
                side="YES",
                best_bid=yes_bid,
                best_ask=yes_ask,
            )
            no_side = MarketSide(
                venue="kalshi",
                market_id=ticker,
                token_id=ticker + "_no",
                side="NO",
                best_bid=no_bid,
                best_ask=no_ask,
            )
            return yes_side, no_side
    except Exception:
        pass
    return None, None


async def place_order(ticker: str, side: str, count: float, price: float) -> str | None:
    """
    Place an order on Kalshi.
    side: "yes" or "no"
    count: number of contracts
    price: limit price in dollars (e.g., 0.65)
    Returns order_id or None.
    """
    if config.paper_mode:
        logger.info(f"[KALSHI] PAPER: {side} {count:.0f} contracts @ ${price:.4f} on {ticker}")
        return "paper_kalshi_order"

    if not config.kalshi_api_key:
        logger.error("[KALSHI] No API key configured")
        return None

    import uuid
    async with httpx.AsyncClient(timeout=15) as client:
        path = "/portfolio/orders"
        order = {
            "ticker": ticker,
            "side": side.lower(),
            "action": "buy",
            "count_fp": f"{count:.2f}",
            "client_order_id": str(uuid.uuid4()),
            "time_in_force": "fill_or_kill",
        }
        # Set price on correct side
        if side.lower() == "yes":
            order["yes_price_dollars"] = f"{price:.4f}"
        else:
            order["no_price_dollars"] = f"{price:.4f}"

        try:
            resp = await client.post(
                f"{BASE_URL}{path}",
                headers=_headers("POST", path),
                json=order,
            )
            if resp.status_code == 200:
                data = resp.json()
                order_id = data.get("order", {}).get("id") or data.get("id")
                if order_id:
                    logger.info(f"[KALSHI] ORDER: {side} {count:.0f} @ ${price:.4f} on {ticker} -> {order_id}")
                    return order_id
            else:
                logger.error(f"[KALSHI] Order failed: {resp.status_code} {resp.text[:200]}")
        except Exception as e:
            logger.error(f"[KALSHI] Order error: {e}")
    return None
