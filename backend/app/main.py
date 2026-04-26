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
async def status():
    import time
    uptime = int(time.time() - _start_time) if _start_time else 0
    stats = await get_stats()
    return {
        "mode": "paper" if config.paper_mode else "live",
        "venues": config.active_venues,
        "uptime_seconds": uptime,
        "markets_tracked": len(_market_pairs),
        "min_spread": config.min_net_spread,
        "max_position": config.max_position_size,
        **stats,
    }


@app.get("/api/opportunities")
async def list_opportunities(limit: int = 50):
    return await get_recent_opportunities(limit)


@app.get("/api/legs")
async def list_legs(limit: int = 50):
    return await get_recent_legs(limit)


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
