"""Multi-venue market scanner — detects arbitrage opportunities."""

import asyncio
import time
import logging
from datetime import datetime, timezone

from app.config import config
from app.models import MarketPair, MarketSide, Opportunity
from app import polymarket_api, jupiter_api, kalshi_api
from app.matcher import match_poly_to_kalshi, titles_match as _titles_match_score
from app.database import save_opportunity
from app import telegram

logger = logging.getLogger("arber")

# Cache of known market pairs
_market_pairs: dict[str, MarketPair] = {}
_last_full_scan: float = 0
FULL_SCAN_INTERVAL = 60  # Re-discover markets every 60 seconds


async def discover_polymarket_markets():
    """Fetch all active Polymarket markets and build MarketPair entries."""
    logger.info("[SCANNER] Discovering Polymarket markets...")
    markets = await polymarket_api.fetch_all_active_markets(limit=100)
    count = 0

    for m in markets:
        condition_id = m.get("conditionId", "")
        if not condition_id:
            continue

        title = m.get("question", "") or m.get("title", "") or m.get("slug", "")
        yes_side, no_side = polymarket_api.parse_market_sides(m)
        if not yes_side or not no_side:
            continue

        key = f"poly_{condition_id}"
        if key not in _market_pairs:
            _market_pairs[key] = MarketPair(
                event_title=title,
                poly_condition_id=condition_id,
            )

        pair = _market_pairs[key]
        pair.sides[("polymarket", "YES")] = yes_side
        pair.sides[("polymarket", "NO")] = no_side
        count += 1

    logger.info(f"[SCANNER] Polymarket: {count} markets loaded")
    return count


async def discover_jupiter_markets():
    """Fetch Jupiter Prediction markets and match to existing pairs."""
    if "jupiter" not in config.active_venues:
        return 0

    logger.info("[SCANNER] Discovering Jupiter markets...")
    count = 0

    # Fetch all active events
    events = await jupiter_api.fetch_active_events()
    for event in events:
        event_id = event.get("id") or event.get("eventId", "")
        title = event.get("title", "")
        markets = event.get("markets", [])
        if not markets:
            markets = await jupiter_api.fetch_event_markets(event_id)

        for m in markets:
            yes_side, no_side = jupiter_api.parse_market_sides(m)
            if not yes_side or not no_side:
                continue

            jup_market_id = yes_side.market_id

            # Try to match with existing Polymarket pair by title
            matched = False
            for key, pair in _market_pairs.items():
                if _titles_match(pair.event_title, title):
                    pair.jup_market_id = jup_market_id
                    pair.sides[("jupiter", "YES")] = yes_side
                    pair.sides[("jupiter", "NO")] = no_side
                    matched = True
                    count += 1
                    break

            if not matched:
                # Create new pair for Jupiter-only market
                key = f"jup_{jup_market_id}"
                _market_pairs[key] = MarketPair(
                    event_title=title,
                    jup_market_id=jup_market_id,
                )
                _market_pairs[key].sides[("jupiter", "YES")] = yes_side
                _market_pairs[key].sides[("jupiter", "NO")] = no_side
                count += 1

    # Fetch degen/crypto events (5m, 15m) — these directly match Polymarket crypto markets
    degen_events = await jupiter_api.fetch_degen_events()
    for event in degen_events:
        event_id = event.get("id") or event.get("eventId", "")
        title = event.get("title", "")
        markets = event.get("markets", [])
        if not markets:
            markets = await jupiter_api.fetch_event_markets(event_id)

        for m in markets:
            yes_side, no_side = jupiter_api.parse_market_sides(m)
            if not yes_side or not no_side:
                continue

            jup_market_id = yes_side.market_id

            # Try to match with Polymarket degen markets by title
            matched = False
            for key, pair in _market_pairs.items():
                if key.startswith("poly_") and _titles_match(pair.event_title, title):
                    pair.jup_market_id = jup_market_id
                    pair.sides[("jupiter", "YES")] = yes_side
                    pair.sides[("jupiter", "NO")] = no_side
                    matched = True
                    count += 1
                    break

            if not matched:
                key = f"jup_degen_{jup_market_id}"
                if key not in _market_pairs:
                    _market_pairs[key] = MarketPair(event_title=title, jup_market_id=jup_market_id)
                _market_pairs[key].sides[("jupiter", "YES")] = yes_side
                _market_pairs[key].sides[("jupiter", "NO")] = no_side
                count += 1

    logger.info(f"[SCANNER] Jupiter: {count} markets loaded")
    return count


async def discover_kalshi_markets():
    """Fetch Kalshi markets and match to existing pairs."""
    if "kalshi" not in config.active_venues:
        return 0

    logger.info("[SCANNER] Discovering Kalshi markets...")
    count = 0

    kalshi_markets = await kalshi_api.fetch_markets(status="open", limit=100)

    for km in kalshi_markets:
        yes_side, no_side = kalshi_api.parse_market_sides(km)
        if not yes_side or not no_side:
            continue

        ticker = km.get("ticker", "")
        title = km.get("title", "")

        # Try to match with existing Polymarket pairs
        matched = False
        for key, pair in _market_pairs.items():
            score = _titles_match_score(pair.event_title, title)
            if score >= 0.6:
                pair.kalshi_market_id = ticker
                pair.sides[("kalshi", "YES")] = yes_side
                pair.sides[("kalshi", "NO")] = no_side
                matched = True
                count += 1
                break

        if not matched:
            key = f"kalshi_{ticker}"
            if key not in _market_pairs:
                _market_pairs[key] = MarketPair(event_title=title, kalshi_market_id=ticker)
            _market_pairs[key].sides[("kalshi", "YES")] = yes_side
            _market_pairs[key].sides[("kalshi", "NO")] = no_side
            count += 1

    logger.info(f"[SCANNER] Kalshi: {count} markets loaded")
    return count


def _titles_match(title_a: str, title_b: str) -> bool:
    """Simple title matching. Returns True if titles are likely the same event."""
    if not title_a or not title_b:
        return False
    # Exact match
    if title_a.lower().strip() == title_b.lower().strip():
        return True
    # Check if one contains the other (for truncated titles)
    a, b = title_a.lower(), title_b.lower()
    if len(a) > 20 and len(b) > 20:
        if a[:30] == b[:30]:
            return True
    return False


async def scan_for_opportunities() -> list[Opportunity]:
    """Scan all market pairs for arbitrage opportunities."""
    global _last_full_scan

    # Periodic full market discovery
    now = time.time()
    if now - _last_full_scan > FULL_SCAN_INTERVAL:
        _last_full_scan = now
        await discover_polymarket_markets()
        await discover_jupiter_markets()
        await discover_kalshi_markets()

    opportunities = []

    for key, pair in _market_pairs.items():
        # Strategy 1: Intra-platform arbitrage
        for venue in config.active_venues:
            total = pair.get_intra_spread(venue)
            if total is not None and total < 1.0:
                fee = _get_venue_fee(venue) * 2  # Both sides
                gross_spread = 1.0 - total
                net_spread = gross_spread - fee
                if net_spread >= config.min_net_spread:
                    yes_side = pair.sides.get((venue, "YES"))
                    no_side = pair.sides.get((venue, "NO"))
                    opp = Opportunity(
                        timestamp=datetime.now(timezone.utc).isoformat(),
                        strategy="intra",
                        event_title=pair.event_title,
                        poly_condition_id=pair.poly_condition_id,
                        jup_market_id=pair.jup_market_id,
                        yes_venue=venue,
                        no_venue=venue,
                        yes_price=yes_side.best_ask if yes_side else 0,
                        no_price=no_side.best_ask if no_side else 0,
                        total_cost=total,
                        gross_spread=gross_spread,
                        net_spread=net_spread,
                        yes_liquidity=yes_side.depth if yes_side else 0,
                        no_liquidity=no_side.depth if no_side else 0,
                    )
                    opportunities.append(opp)

        # Strategy 2+: Cross-venue arbitrage
        if len(config.active_venues) >= 2:
            result = pair.get_best_cross_spread()
            if result:
                total, yes_venue, no_venue = result
                if yes_venue != no_venue:  # Only cross-venue, not same venue
                    yes_fee = _get_venue_fee(yes_venue)
                    no_fee = _get_venue_fee(no_venue)
                    gross_spread = 1.0 - total
                    net_spread = gross_spread - yes_fee - no_fee
                    if net_spread >= config.min_net_spread:
                        yes_side = pair.sides.get((yes_venue, "YES"))
                        no_side = pair.sides.get((no_venue, "NO"))
                        strategy = "cross_chain" if {yes_venue, no_venue} == {"polymarket", "jupiter"} else "cross_platform"
                        opp = Opportunity(
                            timestamp=datetime.now(timezone.utc).isoformat(),
                            strategy=strategy,
                            event_title=pair.event_title,
                            poly_condition_id=pair.poly_condition_id,
                            jup_market_id=pair.jup_market_id,
                            yes_venue=yes_venue,
                            no_venue=no_venue,
                            yes_price=yes_side.best_ask if yes_side else 0,
                            no_price=no_side.best_ask if no_side else 0,
                            total_cost=total,
                            gross_spread=gross_spread,
                            net_spread=net_spread,
                            yes_liquidity=yes_side.depth if yes_side else 0,
                            no_liquidity=no_side.depth if no_side else 0,
                        )
                        opportunities.append(opp)

    return opportunities


def _get_venue_fee(venue: str) -> float:
    if venue == "polymarket":
        return config.poly_fee
    elif venue == "jupiter":
        return config.jupiter_fee
    elif venue == "kalshi":
        return config.kalshi_fee
    return 0.02


async def scan_loop():
    """Main scanning loop — runs continuously."""
    logger.info("[SCANNER] Starting scan loop...")
    await telegram.notify_startup()

    while True:
        try:
            opportunities = await scan_for_opportunities()

            for opp in opportunities:
                opp.id = await save_opportunity(opp)
                logger.info(
                    f"[ARB] {opp.strategy}: {opp.event_title[:40]}... "
                    f"YES@{opp.yes_venue}=${opp.yes_price:.3f} + "
                    f"NO@{opp.no_venue}=${opp.no_price:.3f} = "
                    f"${opp.total_cost:.3f} (net {opp.net_spread*100:.2f}%)"
                )
                await telegram.notify_opportunity(opp)

                # Execute the arb
                from app.executor import execute_arb
                success = await execute_arb(opp)
                if success:
                    logger.info(f"[SCANNER] Arb executed: {opp.event_title[:40]}")
                else:
                    logger.info(f"[SCANNER] Arb skipped/failed: {opp.event_title[:40]}")

        except Exception as e:
            logger.error(f"[SCANNER] Error: {e}")

        await asyncio.sleep(config.scan_interval_ms / 1000)
