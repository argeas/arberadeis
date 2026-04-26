"""Jupiter Prediction Market API client.

Order flow:
1. POST /orders → returns base64 Solana transaction
2. Deserialize + sign with wallet
3. Submit to Solana RPC
4. Poll /orders/status/{pubkey} for fill

Requires: API key from developers.jup.ag/portal + funded Solana wallet
"""

import asyncio
import base64
import logging
import httpx
from app.config import config
from app.models import MarketSide

logger = logging.getLogger("arber")

BASE_URL = "https://api.jup.ag/prediction/v1"

# JupUSD mint address
JUPUSD_MINT = "JuprjznTrTSp2UFa3ZBUFgwdAmtZCq4MQCwysN55USD"
USDC_MINT = "EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v"


def _headers() -> dict:
    """API headers with key."""
    h = {"Content-Type": "application/json"}
    if config.jupiter_api_key:
        h["x-api-key"] = config.jupiter_api_key
    return h


async def fetch_active_events(category: str = None, limit: int = 50) -> list[dict]:
    """Fetch active events from Jupiter Prediction."""
    async with httpx.AsyncClient(timeout=15) as client:
        params = {"status": "active", "limit": limit}
        if category:
            params["category"] = category
        try:
            resp = await client.get(f"{BASE_URL}/events", params=params, headers=_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data if isinstance(data, list) else data.get("events", data.get("data", []))
        except Exception as e:
            logger.warning(f"[JUP] Fetch events error: {e}")
    return []


async def fetch_degen_events() -> list[dict]:
    """Fetch live crypto degen events (5m, 15m intervals — same as polybot markets)."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BASE_URL}/events/degen", headers=_headers())
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
            resp = await client.get(f"{BASE_URL}/events/degen/{symbol}", headers=_headers())
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"[JUP] Fetch degen/{symbol} error: {e}")
    return None


async def fetch_event_markets(event_id: str) -> list[dict]:
    """Fetch markets for a specific event."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BASE_URL}/events/{event_id}/markets", headers=_headers())
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
            resp = await client.get(f"{BASE_URL}/orderbook/{market_id}", headers=_headers())
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"[JUP] Orderbook error: {e}")
    return {}


async def get_market_prices(market_id: str) -> dict:
    """Get current YES/NO prices for a market."""
    async with httpx.AsyncClient(timeout=5) as client:
        try:
            resp = await client.get(f"{BASE_URL}/markets/{market_id}", headers=_headers())
            if resp.status_code == 200:
                return resp.json()
        except Exception as e:
            logger.warning(f"[JUP] Market price error: {e}")
    return {}


def parse_market_sides(market: dict) -> tuple[MarketSide | None, MarketSide | None]:
    """Parse a Jupiter market into YES/NO MarketSide objects."""
    try:
        market_id = market.get("id") or market.get("marketId") or market.get("pubkey", "")

        # Try different price field names
        yes_price = float(
            market.get("buyYesPriceUsd", 0) or
            market.get("yesPrice", 0) or
            market.get("yes_price", 0) or 0
        )
        no_price = float(
            market.get("buyNoPriceUsd", 0) or
            market.get("noPrice", 0) or
            market.get("no_price", 0) or 0
        )

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


async def create_order(market_id: str, side: str, size_usd: float, price: float) -> str | None:
    """
    Create and submit an order on Jupiter Prediction.

    Flow:
    1. POST /orders to get a Solana transaction
    2. Sign with wallet
    3. Submit to Solana RPC
    4. Return order pubkey

    Returns order_pubkey or None.
    """
    if config.paper_mode:
        logger.info(f"[JUP] PAPER: {side} ${size_usd:.2f} @ ${price:.3f} on {market_id}")
        return "paper_jup_order"

    if not config.solana_private_key:
        logger.error("[JUP] No Solana private key configured")
        return None

    try:
        # Step 1: Create order via API
        is_yes = side.upper() == "YES"
        deposit_amount = int(size_usd * 1_000_000)  # USDC has 6 decimals

        # Get wallet pubkey from private key
        wallet_pubkey = _get_wallet_pubkey()
        if not wallet_pubkey:
            return None

        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{BASE_URL}/orders", headers=_headers(), json={
                "ownerPubkey": wallet_pubkey,
                "depositAmount": str(deposit_amount),
                "depositMint": USDC_MINT,
                "marketId": market_id,
                "isYes": is_yes,
                "isBuy": True,
            })

            if resp.status_code != 200:
                logger.error(f"[JUP] Order creation failed: {resp.status_code} {resp.text[:200]}")
                return None

            data = resp.json()
            tx_base64 = data.get("transaction")
            order_pubkey = data.get("order", {}).get("orderPubkey")

            if not tx_base64:
                logger.error(f"[JUP] No transaction in response")
                return None

        # Step 2: Sign and submit
        signature = await _sign_and_submit(tx_base64)
        if not signature:
            return None

        logger.info(f"[JUP] ORDER: {side} ${size_usd:.2f} on {market_id} | sig={signature[:20]}... order={order_pubkey}")
        return order_pubkey

    except Exception as e:
        logger.error(f"[JUP] Order failed: {e}")
        return None


async def check_order_status(order_pubkey: str) -> str:
    """Check order fill status. Returns 'pending', 'filled', or 'failed'."""
    async with httpx.AsyncClient(timeout=10) as client:
        try:
            resp = await client.get(f"{BASE_URL}/orders/status/{order_pubkey}", headers=_headers())
            if resp.status_code == 200:
                data = resp.json()
                return data.get("status", "pending")
        except Exception:
            pass
    return "pending"


def _get_wallet_pubkey() -> str | None:
    """Derive Solana public key from private key."""
    if not config.solana_private_key:
        return None
    try:
        from solders.keypair import Keypair
        if config.solana_private_key.startswith("["):
            # Byte array format
            import json
            kp = Keypair.from_bytes(bytes(json.loads(config.solana_private_key)))
        else:
            # Base58 format
            kp = Keypair.from_base58_string(config.solana_private_key)
        return str(kp.pubkey())
    except ImportError:
        logger.warning("[JUP] solders not installed — cannot derive wallet pubkey")
        return None
    except Exception as e:
        logger.error(f"[JUP] Wallet pubkey derivation failed: {e}")
        return None


async def _sign_and_submit(tx_base64: str) -> str | None:
    """Sign a base64 Solana transaction and submit to RPC."""
    try:
        from solders.keypair import Keypair
        from solders.transaction import VersionedTransaction
        import json

        # Parse private key
        if config.solana_private_key.startswith("["):
            kp = Keypair.from_bytes(bytes(json.loads(config.solana_private_key)))
        else:
            kp = Keypair.from_base58_string(config.solana_private_key)

        # Deserialize transaction
        tx_bytes = base64.b64decode(tx_base64)
        tx = VersionedTransaction.from_bytes(tx_bytes)

        # Sign
        tx.sign([kp])

        # Submit to Solana RPC
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(config.solana_rpc_url, json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "sendTransaction",
                "params": [
                    base64.b64encode(bytes(tx)).decode(),
                    {"encoding": "base64", "skipPreflight": True, "maxRetries": 0}
                ],
            })
            if resp.status_code == 200:
                result = resp.json()
                if "result" in result:
                    return result["result"]  # Transaction signature
                else:
                    logger.error(f"[JUP] RPC error: {result.get('error', 'unknown')}")

    except ImportError:
        logger.error("[JUP] solders not installed — pip install solders")
    except Exception as e:
        logger.error(f"[JUP] Sign+submit failed: {e}")
    return None
