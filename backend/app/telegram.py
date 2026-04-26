"""Telegram notifications for ArberAdeis."""

import httpx
from app.config import config

API_URL = f"https://api.telegram.org/bot{config.telegram_bot_token}"


async def send(text: str):
    if not config.telegram_bot_token or not config.telegram_chat_id:
        return
    try:
        async with httpx.AsyncClient(timeout=10) as client:
            await client.post(f"{API_URL}/sendMessage", json={
                "chat_id": config.telegram_chat_id,
                "text": text,
                "parse_mode": "HTML",
            })
    except Exception:
        pass


async def notify_opportunity(opp):
    mode = "PAPER" if config.paper_mode else "LIVE"
    await send(
        f"🎯 <b>[{mode}] Arb Detected</b>\n"
        f"Strategy: {opp.strategy}\n"
        f"Event: {opp.event_title[:60]}\n"
        f"YES: {opp.yes_venue} @ ${opp.yes_price:.3f}\n"
        f"NO: {opp.no_venue} @ ${opp.no_price:.3f}\n"
        f"Total: ${opp.total_cost:.3f} | Spread: {opp.net_spread*100:.2f}%"
    )


async def notify_execution(opp, leg1, leg2):
    mode = "PAPER" if config.paper_mode else "LIVE"
    await send(
        f"✅ <b>[{mode}] Arb Executed</b>\n"
        f"Event: {opp.event_title[:60]}\n"
        f"Leg 1: {leg1.venue} {leg1.side} @ ${leg1.price:.3f}\n"
        f"Leg 2: {leg2.venue} {leg2.side} @ ${leg2.price:.3f}\n"
        f"Cost: ${opp.total_cost:.3f} | Net: {opp.net_spread*100:.2f}%\n"
        f"Execution: {opp.execution_time_ms}ms"
    )


async def notify_orphan(leg):
    await send(
        f"⚠️ <b>ORPHAN POSITION</b>\n"
        f"Venue: {leg.venue} | Side: {leg.side}\n"
        f"Size: ${leg.size:.2f} @ ${leg.price:.3f}\n"
        f"Leg 2 failed — attempting exit"
    )


async def notify_halt(reason: str, daily_pnl: float):
    await send(
        f"🛑 <b>TRADING HALTED</b>\n"
        f"Reason: {reason}\n"
        f"Daily P&L: ${daily_pnl:+.2f}"
    )


async def notify_startup():
    venues = ", ".join(config.active_venues) or "none"
    mode = "PAPER" if config.paper_mode else "LIVE"
    await send(
        f"🤖 <b>ArberAdeis Started</b>\n"
        f"Mode: {mode}\n"
        f"Venues: {venues}\n"
        f"Min spread: {config.min_net_spread*100:.1f}%\n"
        f"Max position: ${config.max_position_size:.0f}"
    )
