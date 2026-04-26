"""SQLite database for arbitrage trade logging. Separate DBs for paper and live."""

import aiosqlite
from pathlib import Path
from app.models import Opportunity, ArbLeg
from app.config import config

PAPER_DB = Path("data/arb_paper.db")
LIVE_DB = Path("data/arb_live.db")


def _db_path() -> Path:
    return PAPER_DB if config.paper_mode else LIVE_DB


async def _create_tables(db_path: Path):
    """Create all tables in a database."""
    async with aiosqlite.connect(db_path) as db:
        await db.execute("""
            CREATE TABLE IF NOT EXISTS opportunities (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                strategy TEXT NOT NULL,
                event_title TEXT,
                poly_condition_id TEXT,
                jup_market_id TEXT,
                kalshi_market_id TEXT,
                yes_venue TEXT,
                no_venue TEXT,
                yes_price REAL,
                no_price REAL,
                total_cost REAL,
                gross_spread REAL,
                net_spread REAL,
                yes_liquidity REAL,
                no_liquidity REAL,
                status TEXT DEFAULT 'detected',
                skip_reason TEXT,
                execution_time_ms INTEGER
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS arb_legs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                opportunity_id INTEGER,
                timestamp TEXT NOT NULL,
                leg INTEGER,
                venue TEXT,
                chain TEXT,
                side TEXT,
                token_id TEXT,
                price REAL,
                size REAL,
                order_id TEXT,
                tx_hash TEXT,
                status TEXT DEFAULT 'pending',
                fill_price REAL,
                fees REAL,
                pnl REAL,
                jito_bundle_id TEXT
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS orphans (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                leg_id INTEGER,
                timestamp TEXT NOT NULL,
                venue TEXT,
                side TEXT,
                size REAL,
                entry_price REAL,
                exit_price REAL,
                exit_timestamp TEXT,
                pnl REAL,
                status TEXT DEFAULT 'open'
            )
        """)
        await db.execute("""
            CREATE TABLE IF NOT EXISTS portfolio (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp TEXT NOT NULL,
                poly_balance REAL,
                jup_balance REAL,
                kalshi_balance REAL,
                total_value REAL,
                daily_pnl REAL,
                open_positions INTEGER,
                orphan_count INTEGER
            )
        """)
        await db.commit()


async def init_db():
    """Initialize both paper and live databases."""
    PAPER_DB.parent.mkdir(parents=True, exist_ok=True)
    await _create_tables(PAPER_DB)
    await _create_tables(LIVE_DB)


async def save_opportunity(opp: Opportunity) -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("""
            INSERT INTO opportunities (timestamp, strategy, event_title,
                poly_condition_id, jup_market_id, kalshi_market_id,
                yes_venue, no_venue, yes_price, no_price, total_cost,
                gross_spread, net_spread, yes_liquidity, no_liquidity,
                status, skip_reason, execution_time_ms)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            opp.timestamp, opp.strategy, opp.event_title,
            opp.poly_condition_id, opp.jup_market_id, opp.kalshi_market_id,
            opp.yes_venue, opp.no_venue, opp.yes_price, opp.no_price,
            opp.total_cost, opp.gross_spread, opp.net_spread,
            opp.yes_liquidity, opp.no_liquidity, opp.status,
            opp.skip_reason, opp.execution_time_ms,
        ))
        await db.commit()
        return cursor.lastrowid


async def save_leg(leg: ArbLeg) -> int:
    async with aiosqlite.connect(_db_path()) as db:
        cursor = await db.execute("""
            INSERT INTO arb_legs (opportunity_id, timestamp, leg, venue, chain,
                side, token_id, price, size, order_id, tx_hash, status,
                fill_price, fees, pnl, jito_bundle_id)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
        """, (
            leg.opportunity_id, leg.timestamp, leg.leg, leg.venue, leg.chain,
            leg.side, leg.token_id, leg.price, leg.size, leg.order_id,
            leg.tx_hash, leg.status, leg.fill_price, leg.fees, leg.pnl,
            leg.jito_bundle_id,
        ))
        await db.commit()
        return cursor.lastrowid


async def update_opportunity_status(opp_id: int, status: str, execution_time_ms: int = None):
    async with aiosqlite.connect(_db_path()) as db:
        if execution_time_ms is not None:
            await db.execute("UPDATE opportunities SET status=?, execution_time_ms=? WHERE id=?",
                             (status, execution_time_ms, opp_id))
        else:
            await db.execute("UPDATE opportunities SET status=? WHERE id=?", (status, opp_id))
        await db.commit()


async def update_leg_status(leg_id: int, status: str, fill_price: float = None, pnl: float = None):
    async with aiosqlite.connect(_db_path()) as db:
        await db.execute("UPDATE arb_legs SET status=?, fill_price=?, pnl=? WHERE id=?",
                         (status, fill_price, pnl, leg_id))
        await db.commit()


async def get_recent_opportunities(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM opportunities ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in await cursor.fetchall()]


async def get_recent_legs(limit: int = 50) -> list[dict]:
    async with aiosqlite.connect(_db_path()) as db:
        db.row_factory = aiosqlite.Row
        cursor = await db.execute(
            "SELECT * FROM arb_legs ORDER BY id DESC LIMIT ?", (limit,))
        return [dict(row) for row in await cursor.fetchall()]


async def get_stats() -> dict:
    async with aiosqlite.connect(_db_path()) as db:
        total = (await (await db.execute("SELECT COUNT(*) FROM opportunities")).fetchone())[0]
        executed = (await (await db.execute("SELECT COUNT(*) FROM opportunities WHERE status='executed'")).fetchone())[0]
        skipped = (await (await db.execute("SELECT COUNT(*) FROM opportunities WHERE status='skipped'")).fetchone())[0]
        pnl = (await (await db.execute("SELECT COALESCE(SUM(pnl),0) FROM arb_legs WHERE pnl IS NOT NULL")).fetchone())[0]
        orphans = (await (await db.execute("SELECT COUNT(*) FROM orphans WHERE status='open'")).fetchone())[0]
        return {
            "total_opportunities": total,
            "executed": executed,
            "skipped": skipped,
            "total_pnl": round(pnl, 4),
            "open_orphans": orphans,
        }
