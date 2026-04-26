"""Polymarket API client — market discovery and orderbook reading."""

import asyncio
import json
import logging
import httpx
from app.config import config
from app.models import MarketSide

logger = logging.getLogger("arber")

GAMMA_API = "https://gamma-api.polymarket.com"
CLOB_API = "https://clob.polymarket.com"

_clob_client = None


def _get_clob_client():
    """Lazily initialize the CLOB client for order placement."""
    global _clob_client
    if _clob_client is not None:
        return _clob_client

    if not config.poly_api_key or not config.poly_private_key:
        return None

    try:
        from py_clob_client.client import ClobClient
        from py_clob_client.clob_types import ApiCreds, BuilderConfig

        creds = ApiCreds(
            api_key=config.poly_api_key,
            api_secret=config.poly_api_secret,
            api_passphrase=config.poly_api_passphrase,
        )
        builder = BuilderConfig(
            api_key=config.builder_api_key,
            api_secret=config.builder_api_secret,
            api_passphrase=config.builder_api_passphrase,
        )
        _clob_client = ClobClient(
            host=CLOB_API,
            key=config.poly_private_key,
            chain_id=137,
            creds=creds,
            builder=builder,
            signature_type=2,
            funder=config.poly_proxy_address,
        )
        logger.info(f"[POLY] CLOB client initialized. Funder: {config.poly_proxy_address}")
        return _clob_client
    except Exception as e:
        logger.error(f"[POLY] Client init failed: {e}")
        return None


async def fetch_all_active_markets(limit: int = 100) -> list[dict]:
    """Fetch all active binary markets from Polymarket."""
    markets = []
    async with httpx.AsyncClient(timeout=15) as client:
        # Fetch active events
        offset = 0
        while True:
            try:
                resp = await client.get(f"{GAMMA_API}/markets", params={
                    "active": "true",
                    "closed": "false",
                    "limit": limit,
                    "offset": offset,
                })
                if resp.status_code != 200:
                    break
                batch = resp.json()
                if not batch:
                    break
                markets.extend(batch)
                if len(batch) < limit:
                    break
                offset += limit
            except Exception as e:
                logger.warning(f"[POLY] Fetch error at offset {offset}: {e}")
                break

    return markets


def parse_market_sides(market: dict) -> tuple[MarketSide | None, MarketSide | None]:
    """Parse a Gamma API market into YES/NO MarketSide objects."""
    try:
        token_ids = market.get("clobTokenIds", "[]")
        if isinstance(token_ids, str):
            token_ids = json.loads(token_ids)
        outcomes = market.get("outcomes", '["Yes","No"]')
        if isinstance(outcomes, str):
            outcomes = json.loads(outcomes)
        outcome_prices = market.get("outcomePrices", "[0.5,0.5]")
        if isinstance(outcome_prices, str):
            outcome_prices = json.loads(outcome_prices)

        if len(token_ids) < 2 or len(outcomes) < 2:
            return None, None

        # Determine YES/NO index
        yes_idx = 0 if outcomes[0].lower() in ("yes", "up") else 1
        no_idx = 1 - yes_idx

        yes_side = MarketSide(
            venue="polymarket",
            market_id=market.get("conditionId", ""),
            token_id=token_ids[yes_idx],
            side="YES",
            best_ask=float(outcome_prices[yes_idx]) if outcome_prices else 0.5,
        )
        no_side = MarketSide(
            venue="polymarket",
            market_id=market.get("conditionId", ""),
            token_id=token_ids[no_idx],
            side="NO",
            best_ask=float(outcome_prices[no_idx]) if outcome_prices else 0.5,
        )
        return yes_side, no_side
    except Exception:
        return None, None


async def get_orderbook(token_id: str) -> dict:
    """Get orderbook for a token. Returns {bids: [...], asks: [...]}."""
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{CLOB_API}/book", params={"token_id": token_id})
            if resp.status_code == 200:
                return resp.json()
        except Exception:
            pass
    return {"bids": [], "asks": []}


async def get_best_ask(token_id: str) -> tuple[float, float]:
    """Get best ask price and depth for a token. Returns (price, depth_usd)."""
    book = await get_orderbook(token_id)
    asks = book.get("asks", [])
    if not asks:
        return 0.0, 0.0
    # Asks sorted ascending by price
    best = sorted(asks, key=lambda x: float(x.get("price", 999)))[0]
    price = float(best.get("price", 0))
    size = float(best.get("size", 0))
    return price, price * size


async def place_order(token_id: str, side: str, size: float, price: float) -> str | None:
    """Place a FOK order on Polymarket. Returns order_id or None."""
    if config.paper_mode:
        logger.info(f"[POLY] PAPER: {side} {size:.2f} shares @ ${price:.3f}")
        return "paper_order"

    client = _get_clob_client()
    if not client:
        return None

    try:
        from py_clob_client.order_builder.constants import BUY
        from py_clob_client.clob_types import OrderArgs, OrderType

        order_args = OrderArgs(price=price, size=size, side=BUY, token_id=token_id)
        loop = asyncio.get_event_loop()
        signed = await loop.run_in_executor(None, client.create_order, order_args)
        resp = await loop.run_in_executor(None, client.post_order, signed, OrderType.FOK)

        if isinstance(resp, dict):
            order_id = resp.get("orderID") or resp.get("id")
            if order_id:
                logger.info(f"[POLY] ORDER: {side} {size:.2f} @ ${price:.3f} -> {order_id}")
                return order_id

        return None
    except Exception as e:
        logger.error(f"[POLY] Order failed: {e}")
        return None
