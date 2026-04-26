"""Jupiter Prediction Market API client."""

import logging
import httpx
from app.config import config
from app.models import MarketSide

logger = logging.getLogger("arber")

BASE_URL = "https://prediction-market-api.jup.ag/api/v1"


async def fetch_active_events(category: str = None, limit: int = 50) -> list[dict]:
    """Fetch active events from Jupiter Prediction."""
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"status": "active", "limit": limit}
        if category:
            params["category"] = category
        try:
            resp = await client.get(f"{BASE_URL}/events", params=params)
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("events", data.get("data", []))
        except Exception as e:
            logger.warning(f"[JUP] Fetch events error: {e}")
    return []


async def fetch_degen_events() -> list[dict]:
    """Fetch live crypto degen events (5m, 15m intervals — same as polybot)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BASE_URL}/events/degen")
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("events", data.get("data", []))
        except Exception as e:
            logger.warning(f"[JUP] Fetch degen error: {e}")
    return []


async def fetch_degen_by_symbol(symbol: str) -> dict | None:
    """Fetch current live degen event for a specific asset (BTC, ETH, SOL)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BASE_URL}/events/degen/{symbol}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"[JUP] Fetch degen/{symbol} error: {e}")
    return None


async def fetch_event_markets(event_id: str) -> list[dict]:
    """Fetch markets for a specific event."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BASE_URL}/events/{event_id}/markets")
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("markets", [])
        except Exception as e:
            logger.warning(f"[JUP] Fetch markets error: {e}")
    return []


async def get_orderbook(market_id: str) -> dict:
    """Get orderbook data for a Jupiter market."""
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{BASE_URL}/orderbook/{market_id}")
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"[JUP] Orderbook error: {e}")
    return {}


def parse_market_sides(market: dict) -> tuple[MarketSide | None, MarketSide | None]:
    """Parse a Jupiter market into YES/NO MarketSide objects."""
    try:
        market_id = market.get("id") or market.get("marketId") or market.get("pubkey", "")
        yes_price = float(market.get("yesPrice", 0) or market.get("yes_price", 0) or 0)
        no_price = float(market.get("noPrice", 0) or market.get("no_price", 0) or 0)

        # If only one price, derive the other
        if yes_price > 0 and no_price == 0:
            no_price = 1.0 - yes_price
        elif no_price > 0 and yes_price == 0:
            yes_price = 1.0 - no_price

        if market_id and (yes_price > 0 or no_price > 0):
            yes_side = MarketSide(
                venue="jupiter",
                market_id=market_id,
                token_id=market.get("yesTokenMint", market_id + "_yes"),
                side="YES",
                best_ask=yes_price,
            )
            no_side = MarketSide(
                venue="jupiter",
                market_id=market_id,
                token_id=market.get("noTokenMint", market_id + "_no"),
                side="NO",
                best_ask=no_price,
            )
            return yes_side, no_side
    except Exception:
        pass
    return None, None


async def create_order(market_id: str, side: str, size: float, price: float) -> str | None:
    """Create an order on Jupiter Prediction. Returns transaction or None."""
    if config.paper_mode:
        logger.info(f"[JUP] PAPER: {side} ${size:.2f} @ ${price:.3f} on {market_id}")
        return "paper_order"

    # TODO: Implement Solana transaction signing + Jito bundle
    # For now, use Jupiter's REST API to create order
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.post(f"{BASE_URL}/orders", json={
                "marketId": market_id,
                "side": side.lower(),
                "size": size,
                "price": price,
            })
            if resp.status_code == 200:
                data = resp.json()
                return data.get("orderId") or data.get("id") or data.get("signature")
        except Exception as e:
            logger.error(f"[JUP] Order failed: {e}")
    return None
