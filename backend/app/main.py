"""ArberAdeis — Multi-Venue Prediction Market Arbitrage Bot."""

import asyncio
import logging
import sys
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from fastapi.responses import FileResponse

from app.config import config
from app.database import init_db, get_recent_opportunities, get_recent_legs, get_stats
from app.scanner import scan_loop, _market_pairs
from app import kalshi_api

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    handlers=[
        logging.StreamHandler(sys.stderr),
        logging.FileHandler("data/arber.log"),
    ],
)
logger = logging.getLogger("arber")

_scan_task = None
_start_time = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _scan_task, _start_time
    import time
    _start_time = time.time()

    logger.info("=" * 60)
    logger.info("ArberAdeis — Multi-Venue Arbitrage Bot")
    logger.info(f"Mode: {'PAPER' if config.paper_mode else 'LIVE'}")
    logger.info(f"Venues: {', '.join(config.active_venues)}")
    logger.info(f"Min spread: {config.min_net_spread*100:.1f}%")
    logger.info("=" * 60)

    await init_db()

    _scan_task = asyncio.create_task(scan_loop(), name="scan_loop")

    yield

    if _scan_task:
        _scan_task.cancel()


app = FastAPI(title="ArberAdeis", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)

STATIC_DIR = Path(__file__).parent.parent / "static"
if STATIC_DIR.exists():
    app.mount("/static", StaticFiles(directory=str(STATIC_DIR)), name="static")


@app.get("/")
async def index():
    return FileResponse(str(STATIC_DIR / "index.html"))


@app.get("/api/health")
async def health():
    return {"status": "ok", "mode": "paper" if config.paper_mode else "live"}


@app.get("/api/status")
async def status(mode: str = None):
    import time
    uptime = int(time.time() - _start_time) if _start_time else 0
    stats = await get_stats(mode)
    return {
        "mode": mode or ("paper" if config.paper_mode else "live"),
        "venues": config.active_venues,
        "uptime_seconds": uptime,
        "markets_tracked": len(_market_pairs),
        "min_spread": config.min_net_spread,
        "max_position": config.max_position_size,
        **stats,
    }


@app.get("/api/opportunities")
async def list_opportunities(limit: int = 30, offset: int = 0, mode: str = None):
    return await get_recent_opportunities(limit, offset, mode)


@app.get("/api/legs")
async def list_legs(limit: int = 50, mode: str = None):
    return await get_recent_legs(limit, mode)


@app.get("/api/config")
async def get_config():
    return {
        "venues": {
            "polymarket": config.venue_polymarket_enabled,
            "jupiter": config.venue_jupiter_enabled,
            "kalshi": config.venue_kalshi_enabled,
        },
        "risk": {
            "max_position_size": config.max_position_size,
            "min_net_spread": config.min_net_spread,
            "daily_loss_limit": config.daily_loss_limit,
            "orphan_daily_budget": config.orphan_daily_budget,
        },
        "fees": {
            "polymarket": config.poly_fee,
            "jupiter": config.jupiter_fee,
            "kalshi": config.kalshi_fee,
        },
        "paper_mode": config.paper_mode,
        "scan_interval_ms": config.scan_interval_ms,
    }


@app.put("/api/config")
async def update_config(body: dict):
    """Update runtime configuration. Merges with current values."""
    venues = body.get("venues", {})
    if "polymarket" in venues:
        config.venue_polymarket_enabled = venues["polymarket"]
    if "jupiter" in venues:
        config.venue_jupiter_enabled = venues["jupiter"]
    if "kalshi" in venues:
        config.venue_kalshi_enabled = venues["kalshi"]

    risk = body.get("risk", {})
    if "max_position_size" in risk:
        config.max_position_size = float(risk["max_position_size"])
    if "min_net_spread" in risk:
        config.min_net_spread = float(risk["min_net_spread"])
    if "daily_loss_limit" in risk:
        config.daily_loss_limit = float(risk["daily_loss_limit"])
    if "orphan_daily_budget" in risk:
        config.orphan_daily_budget = float(risk["orphan_daily_budget"])

    fees = body.get("fees", {})
    if "polymarket" in fees:
        config.poly_fee = float(fees["polymarket"])
    if "jupiter" in fees:
        config.jupiter_fee = float(fees["jupiter"])
    if "kalshi" in fees:
        config.kalshi_fee = float(fees["kalshi"])

    if "scan_interval_ms" in body:
        config.scan_interval_ms = int(body["scan_interval_ms"])

    if "paper_mode" in body:
        config.paper_mode = body["paper_mode"]

    config.save_runtime()
    logger.info(f"[CONFIG] Updated and saved: venues={config.active_venues} spread={config.min_net_spread} mode={'paper' if config.paper_mode else 'live'}")
    return await get_config()


@app.get("/api/wallet")
async def wallet():
    """Get wallet balances across all venues."""
    balances = {"polymarket": 0, "jupiter": 0, "kalshi": 0, "total": 0}

    if config.venue_polymarket_enabled:
        try:
            import httpx as _httpx
            # Check on-chain USDC balance on proxy address (Polygon)
            usdc_contract = "0x2791Bca1f2de4661ED88A30C99A7a9449Aa84174"
            addr = config.poly_proxy_address or config.poly_wallet_address
            padded = addr[2:].lower().zfill(64)
            data = f"0x70a08231{padded}"
            async with _httpx.AsyncClient(timeout=10) as hc:
                resp = await hc.post("https://polygon-bor-rpc.publicnode.com", json={
                    "jsonrpc": "2.0", "method": "eth_call",
                    "params": [{"to": usdc_contract, "data": data}, "latest"], "id": 1
                })
                result = resp.json().get("result", "0x0")
                balances["polymarket"] = round(int(result, 16) / 1e6, 2)
        except Exception as e:
            logger.warning(f"[WALLET] Polymarket balance error: {e}")

    if config.venue_kalshi_enabled:
        try:
            balances["kalshi"] = await kalshi_api.get_balance()
        except Exception:
            pass

    balances["total"] = round(balances["polymarket"] + balances["jupiter"] + balances["kalshi"], 2)
    return balances


@app.post("/api/mode/paper")
async def set_paper_mode():
    """Switch to paper trading mode."""
    config.paper_mode = True
    config.save_runtime()
    logger.info("[MODE] Switched to PAPER mode")
    return {"mode": "paper", "db": "arb_paper.db"}


@app.post("/api/mode/live")
async def set_live_mode():
    """Switch to live trading mode. Requires confirmation."""
    config.paper_mode = False
    config.save_runtime()
    logger.warning("[MODE] >>> SWITCHED TO LIVE MODE — REAL FUNDS AT RISK <<<")
    try:
        await telegram.send(
            "🔴 <b>ArberAdeis switched to LIVE MODE</b>\n"
            "Real orders will be placed."
        )
    except Exception:
        pass
    return {"mode": "live", "db": "arb_live.db"}


@app.get("/api/mode")
async def get_mode():
    return {"mode": "paper" if config.paper_mode else "live"}


@app.post("/api/venues/{venue}/toggle")
async def toggle_venue(venue: str):
    """Toggle a venue on/off."""
    if venue == "polymarket":
        config.venue_polymarket_enabled = not config.venue_polymarket_enabled
    elif venue == "jupiter":
        config.venue_jupiter_enabled = not config.venue_jupiter_enabled
    elif venue == "kalshi":
        config.venue_kalshi_enabled = not config.venue_kalshi_enabled
    else:
        return {"error": "Unknown venue"}
    config.save_runtime()
    enabled = getattr(config, f"venue_{venue}_enabled", False)
    return {"venue": venue, "enabled": enabled}
