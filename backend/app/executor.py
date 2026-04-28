"""Dual-leg arbitrage execution engine."""

import asyncio
import time
import logging
from datetime import datetime, timezone

from app.config import config
from app.models import Opportunity, ArbLeg, PortfolioState
from app import polymarket_api, jupiter_api, kalshi_api
from app.database import save_leg, update_leg_status, update_opportunity_status
from app import telegram

logger = logging.getLogger("arber")

# Portfolio state
portfolio = PortfolioState()

# Track daily orphan losses
_daily_orphan_loss: float = 0.0


async def execute_arb(opp: Opportunity) -> bool:
    """
    Execute a two-leg arbitrage trade.
    Returns True if both legs filled, False otherwise.
    """
    if portfolio.halted:
        logger.warning(f"[EXEC] Halted: {portfolio.halt_reason}")
        return False

    # Check daily loss limit
    if abs(portfolio.daily_pnl) >= config.daily_loss_limit:
        portfolio.halted = True
        portfolio.halt_reason = f"Daily loss limit ${config.daily_loss_limit} reached"
        await telegram.notify_halt(portfolio.halt_reason, portfolio.daily_pnl)
        return False

    # Check orphan budget
    if _daily_orphan_loss >= config.orphan_daily_budget:
        logger.warning(f"[EXEC] Orphan budget exhausted (${_daily_orphan_loss:.2f})")
        return False

    # Calculate position size
    # Use max_position_size as base; if liquidity data available, cap to it
    size_usd = config.max_position_size
    if opp.yes_liquidity > 0:
        size_usd = min(size_usd, opp.yes_liquidity)
    if opp.no_liquidity > 0:
        size_usd = min(size_usd, opp.no_liquidity)
    if size_usd < 1:
        await update_opportunity_status(opp.id, "skipped")
        return False

    start_time = time.time()

    # Determine leg order — prefer faster venue first
    # Jupiter (Solana ~400ms) before Polymarket (Polygon ~2s) before Kalshi
    legs = _order_legs(opp)

    # Execute leg 1
    leg1 = ArbLeg(
        opportunity_id=opp.id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        leg=1,
        venue=legs[0]["venue"],
        chain=_venue_chain(legs[0]["venue"]),
        side=legs[0]["side"],
        token_id=legs[0]["token_id"],
        price=legs[0]["price"],
        size=size_usd / legs[0]["price"],
        status="pending",
    )
    leg1.id = await save_leg(leg1)

    order1 = await _place_order(leg1)
    if not order1:
        leg1.status = "failed"
        await update_leg_status(leg1.id, "failed")
        await update_opportunity_status(opp.id, "failed")
        logger.warning(f"[EXEC] Leg 1 failed: {leg1.venue} {leg1.side}")
        return False

    leg1.order_id = order1
    leg1.status = "filled"
    await update_leg_status(leg1.id, "filled", fill_price=leg1.price)

    # Execute leg 2 immediately
    leg2 = ArbLeg(
        opportunity_id=opp.id,
        timestamp=datetime.now(timezone.utc).isoformat(),
        leg=2,
        venue=legs[1]["venue"],
        chain=_venue_chain(legs[1]["venue"]),
        side=legs[1]["side"],
        token_id=legs[1]["token_id"],
        price=legs[1]["price"],
        size=size_usd / legs[1]["price"],
        status="pending",
    )
    leg2.id = await save_leg(leg2)

    order2 = await _place_order(leg2)
    if not order2:
        # LEG 2 FAILED — we have an orphan
        leg2.status = "failed"
        await update_leg_status(leg2.id, "failed")
        leg1.status = "orphan"
        await update_leg_status(leg1.id, "orphan")
        await update_opportunity_status(opp.id, "failed")

        logger.error(f"[EXEC] ORPHAN! Leg 2 failed. Leg 1 {leg1.venue} {leg1.side} is unhedged")
        await telegram.notify_orphan(leg1)

        # Try to exit the orphan at market
        await _exit_orphan(leg1)
        return False

    leg2.order_id = order2
    leg2.status = "filled"
    await update_leg_status(leg2.id, "filled", fill_price=leg2.price)

    # Both legs filled — calculate P&L
    execution_ms = int((time.time() - start_time) * 1000)
    total_cost = leg1.price * leg1.size + leg2.price * leg2.size
    fees = _calculate_fees(leg1, leg2)
    gross_pnl = min(leg1.size, leg2.size) * 1.0 - total_cost  # Payout $1 per share
    net_pnl = gross_pnl - fees

    # Update P&L on legs
    await update_leg_status(leg1.id, "filled", fill_price=leg1.price, pnl=net_pnl / 2)
    await update_leg_status(leg2.id, "filled", fill_price=leg2.price, pnl=net_pnl / 2)
    await update_opportunity_status(opp.id, "executed", execution_ms)

    portfolio.daily_pnl += net_pnl
    portfolio.total_pnl += net_pnl
    portfolio.open_positions += 1

    opp.execution_time_ms = execution_ms
    await telegram.notify_execution(opp, leg1, leg2)

    logger.info(
        f"[EXEC] SUCCESS: {opp.event_title[:30]}... "
        f"${total_cost:.3f} → $1.00 | Net P&L: ${net_pnl:.4f} | {execution_ms}ms"
    )
    return True


def _order_legs(opp: Opportunity) -> list[dict]:
    """Order legs for execution — faster venue first."""
    yes_side = {"venue": opp.yes_venue, "side": "YES", "price": opp.yes_price, "token_id": opp.yes_token_id}
    no_side = {"venue": opp.no_venue, "side": "NO", "price": opp.no_price, "token_id": opp.no_token_id}

    venue_speed = {"jupiter": 1, "polymarket": 2, "kalshi": 3}
    legs = [yes_side, no_side]
    legs.sort(key=lambda l: venue_speed.get(l["venue"], 99))
    return legs


def _venue_chain(venue: str) -> str:
    return {"polymarket": "polygon", "jupiter": "solana", "kalshi": "centralized"}.get(venue, "unknown")


async def _place_order(leg: ArbLeg) -> str | None:
    """Place an order on the appropriate venue."""
    if leg.venue == "polymarket":
        return await polymarket_api.place_order(leg.token_id, leg.side, leg.size, leg.price)
    elif leg.venue == "jupiter":
        return await jupiter_api.create_order(leg.token_id, leg.side, leg.size, leg.price)
    elif leg.venue == "kalshi":
        return await kalshi_api.place_order(leg.token_id, leg.side, leg.size, leg.price)
    return None


def _calculate_fees(leg1: ArbLeg, leg2: ArbLeg) -> float:
    """Calculate total fees for both legs."""
    fee1 = _venue_fee(leg1.venue) * leg1.price * leg1.size
    fee2 = _venue_fee(leg2.venue) * leg2.price * leg2.size
    return fee1 + fee2


def _venue_fee(venue: str) -> float:
    if venue == "polymarket":
        return config.poly_fee
    elif venue == "jupiter":
        return config.jupiter_fee
    elif venue == "kalshi":
        return config.kalshi_fee
    return 0.02


async def _exit_orphan(leg: ArbLeg):
    """Try to exit an orphan position at market price."""
    global _daily_orphan_loss

    if config.paper_mode:
        logger.info(f"[EXEC] PAPER: Would exit orphan {leg.venue} {leg.side}")
        # Simulate a small loss
        loss = leg.price * leg.size * 0.05  # Assume 5% slippage
        _daily_orphan_loss += loss
        portfolio.daily_pnl -= loss
        return

    # TODO: Implement actual market exit
    # For now, log it
    logger.warning(f"[EXEC] Orphan exit not yet implemented for {leg.venue}")
    _daily_orphan_loss += leg.price * leg.size * 0.1  # Budget 10% loss
