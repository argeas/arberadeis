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
        from py_clob_client_v2.client import ClobClient

        _clob_client = ClobClient(
            host=CLOB_API,
            chain_id=137,
            key=config.poly_private_key,
            signature_type=2,  # GNOSIS_SAFE for proxy wallets
            funder=config.poly_proxy_address,
        )

        # Derive API creds from private key
        creds = _clob_client.derive_api_key()
        _clob_client.set_api_creds(creds)

        logger.info(f"[POLY] V2 CLOB client initialized. Funder: {config.poly_proxy_address}")
        return _clob_client
    except Exception as e:
        logger.error(f"[POLY] Client init failed: {e}")
        import traceback
        logger.error(traceback.format_exc())
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


def is_market_tradeable(market: dict) -> bool:
    """Check if a market is still open and tradeable."""
    import time
    from datetime import datetime

    # Skip closed markets
    if market.get("closed") or not market.get("active"):
        return False

    # Skip markets that resolve within 5 minutes
    end_date = market.get("endDate") or market.get("end_date_iso") or ""
    if end_date:
        try:
            end_ts = datetime.fromisoformat(end_date.replace("Z", "+00:00")).timestamp()
            if end_ts - time.time() < 300:  # Less than 5 min to close
                return False
        except Exception:
            pass

    # Skip if outcome prices are 0/1 (already resolved)
    outcome_prices = market.get("outcomePrices", "")
    if isinstance(outcome_prices, str):
        try:
            prices = json.loads(outcome_prices)
            if prices and (float(prices[0]) >= 0.99 or float(prices[0]) <= 0.01):
                return False
        except Exception:
            pass

    return True


def parse_market_sides(market: dict) -> tuple[MarketSide | None, MarketSide | None]:
    """Parse a Gamma API market into YES/NO MarketSide objects."""
    if not is_market_tradeable(market):
        return None, None
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


async def get_tick_size(token_id: str) -> str:
    """Get the tick size for a market via direct HTTP. Returns as string."""
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{CLOB_API}/tick-size", params={"token_id": token_id})
            if resp.status_code == 200:
                return str(resp.json().get("minimum_tick_size", "0.01"))
        except Exception:
            pass
    return "0.01"


def _round_to_tick(price: float, tick_size: str) -> float:
    """Round price to the nearest valid tick."""
    tick = float(tick_size)
    return round(round(price / tick) * tick, 4)


async def place_order(token_id: str, side: str, size: float, price: float) -> str | None:
    """Place a FOK order on Polymarket. Returns order_id or None."""
    if config.paper_mode:
        logger.info(f"[POLY] PAPER: {side} {size:.2f} shares @ ${price:.3f}")
        return "paper_order"

    client = _get_clob_client()
    if not client:
        return None

    try:
        from py_clob_client_v2.clob_types import OrderArgsV2, OrderType, PartialCreateOrderOptions

        # Tick size as string
        tick_size = await get_tick_size(token_id)
        rounded_price = _round_to_tick(price, tick_size)

        rounded_size = round(size, 2)
        if side.upper() == "BUY":
            rounded_size = max(rounded_size, 5.0)
        rounded_size = float(int(rounded_size))

        options = PartialCreateOrderOptions(tick_size=tick_size, neg_risk=False)

        def _do():
            order = client.create_order(
                OrderArgsV2(
                    token_id=token_id,
                    price=rounded_price,
                    size=rounded_size,
                    side=side.upper(),
                ),
                options=options,
            )
            return client.post_order(order, order_type=OrderType.FOK)

        loop = asyncio.get_event_loop()
        resp = await loop.run_in_executor(None, _do)

        if isinstance(resp, dict):
            order_id = resp.get("orderID") or resp.get("id")
            if order_id:
                logger.info(f"[POLY] ORDER: {side} {rounded_size:.2f} @ ${rounded_price:.4f} (tick={tick_size}) -> {order_id}")
                return order_id
            else:
                logger.error(f"[POLY] Order rejected: {resp}")

        return None
    except Exception as e:
        logger.error(f"[POLY] Order failed: {e}")
        return None
