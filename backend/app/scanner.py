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

# Deduplication: track recently detected opportunities by market key
_recent_opps: dict[str, float] = {}  # key → timestamp of last detection
DEDUP_COOLDOWN = 300  # Don't re-detect same market within 5 minutes
MIN_VOLUME = 1000  # Min market volume USD; lower catches mid-tier markets with wider spreads
MAX_SCAN_MARKETS = 1000  # Top N markets by volume to scan


async def discover_polymarket_markets():
    """Fetch high-volume active Polymarket markets and build MarketPair entries.
    Filters to top markets by volume since arbs only exist where there's real liquidity."""
    logger.info("[SCANNER] Discovering Polymarket markets (high-volume filter)...")
    import httpx, json
    markets = []
    # Fetch top 500 markets by volume — these are where real liquidity is
    async with httpx.AsyncClient(timeout=15) as client:
        try:
            resp = await client.get(
                "https://gamma-api.polymarket.com/markets",
                params={
                    "active": "true", "closed": "false",
                    "order": "volume", "ascending": "false",
                    "limit": MAX_SCAN_MARKETS,
                },
            )
            if resp.status_code == 200:
                markets = resp.json()
        except Exception as e:
            logger.warning(f"[SCANNER] Fetch error: {e}")
            return 0

    count = 0
    for m in markets:
        # Volume filter — skip markets without real activity
        try:
            volume = float(m.get("volume", 0) or 0)
        except Exception:
            volume = 0
        if volume < MIN_VOLUME:
            continue

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

    logger.info(f"[SCANNER] Polymarket: {count} high-volume markets loaded")
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


def _dedup_key(pair_key: str, strategy: str, yes_venue: str, no_venue: str) -> str:
    return f"{pair_key}|{strategy}|{yes_venue}|{no_venue}"


def _is_duplicate(dedup_key: str) -> bool:
    """Check if this opportunity was recently detected. If not, mark it."""
    now = time.time()
    # Clean old entries
    expired = [k for k, t in _recent_opps.items() if now - t > DEDUP_COOLDOWN]
    for k in expired:
        del _recent_opps[k]

    if dedup_key in _recent_opps:
        return True
    _recent_opps[dedup_key] = now
    return False


def _build_opportunity(pair, strategy, yes_venue, no_venue, yes_side, no_side,
                       total, gross_spread, net_spread) -> Opportunity:
    return Opportunity(
        timestamp=datetime.now(timezone.utc).isoformat(),
        strategy=strategy,
        event_title=pair.event_title,
        poly_condition_id=pair.poly_condition_id,
        jup_market_id=pair.jup_market_id,
        yes_venue=yes_venue,
        no_venue=no_venue,
        yes_price=yes_side.best_ask if yes_side else 0,
        no_price=no_side.best_ask if no_side else 0,
        yes_token_id=yes_side.token_id if yes_side else "",
        no_token_id=no_side.token_id if no_side else "",
        total_cost=total,
        gross_spread=gross_spread,
        net_spread=net_spread,
        yes_liquidity=yes_side.depth if yes_side else 0,
        no_liquidity=no_side.depth if no_side else 0,
    )


async def _real_intra_spread(pair: MarketPair, venue: str, target_size: float = 5.0) -> tuple[float, float, float] | None:
    """Read REAL orderbooks for both sides and compute REAL VWAP-based spread.
    Returns (yes_vwap, no_vwap, total) or None if insufficient depth.
    Only Polymarket is supported here (live orderbook reads)."""
    if venue != "polymarket":
        # Fall back to cached prices for other venues
        total = pair.get_intra_spread(venue)
        if total is not None:
            yes = pair.sides.get((venue, "YES"))
            no = pair.sides.get((venue, "NO"))
            return (yes.best_ask if yes else 0, no.best_ask if no else 0, total)
        return None

    yes_side = pair.sides.get((venue, "YES"))
    no_side = pair.sides.get((venue, "NO"))
    if not yes_side or not no_side or not yes_side.token_id or not no_side.token_id:
        return None

    # Fetch both orderbooks in parallel
    import asyncio as _a
    yes_book, no_book = await _a.gather(
        polymarket_api.get_orderbook(yes_side.token_id),
        polymarket_api.get_orderbook(no_side.token_id),
        return_exceptions=True,
    )

    def vwap(book, target):
        if isinstance(book, Exception) or not isinstance(book, dict):
            return None
        asks = book.get("asks", [])
        if not asks:
            return None
        asks_sorted = sorted(asks, key=lambda a: float(a["price"]))
        accumulated = 0.0
        cost = 0.0
        for ask in asks_sorted:
            ask_size = float(ask["size"])
            ask_price = float(ask["price"])
            need = target - accumulated
            take = min(need, ask_size)
            accumulated += take
            cost += take * ask_price
            if accumulated >= target:
                break
        if accumulated < target * 0.5:
            return None
        return cost / accumulated

    yes_vwap = vwap(yes_book, target_size)
    no_vwap = vwap(no_book, target_size)
    if yes_vwap is None or no_vwap is None:
        return None
    return (yes_vwap, no_vwap, yes_vwap + no_vwap)


async def scan_for_opportunities() -> list[Opportunity]:
    """Scan high-volume markets via REAL orderbook data.
    Filters to top markets by volume; reads actual asks (not stale outcomePrices)."""
    global _last_full_scan

    now = time.time()
    if now - _last_full_scan > FULL_SCAN_INTERVAL:
        _last_full_scan = now
        await discover_polymarket_markets()
        await discover_jupiter_markets()
        await discover_kalshi_markets()

    opportunities = []

    # Compute target size in shares for the depth check
    target_shares = config.max_position_size / 0.5  # Worst case: $5 / $0.50 = 10 shares

    # Use cached prices as a CHEAP filter — but accept any spread < $1
    # since cached prices may be off. Real check happens via orderbook below.
    candidates = []
    for key, pair in _market_pairs.items():
        for venue in config.active_venues:
            cached_total = pair.get_intra_spread(venue)
            # Loose filter: any market where cached prices suggest possible spread
            # OR where we can't tell (price=0 means we should still check book)
            if cached_total is None:
                continue
            if cached_total < 1.05:  # Loose: even slightly above $1 cached may have real spread under it
                candidates.append((key, pair, venue))

    # Cap to avoid rate limits — sort by most promising (lowest cached total = best lead)
    candidates.sort(key=lambda c: c[1].get_intra_spread(c[2]) or 999)
    candidates = candidates[:80]
    if candidates:
        logger.info(f"[SCANNER] {len(candidates)} candidates to check; lowest cached total: ${candidates[0][1].get_intra_spread(candidates[0][2]):.3f}")

    for key, pair, venue in candidates:
        dk = _dedup_key(key, "intra", venue, venue)
        if _is_duplicate(dk):
            continue

        # REAL orderbook check — this is the actual arbitrage detection
        result = await _real_intra_spread(pair, venue, target_size=target_shares)
        if result is None:
            continue
        yes_vwap, no_vwap, total = result

        if total >= 1.0:
            continue  # No arb after VWAP

        fee = _get_venue_fee(venue) * 2
        gross_spread = 1.0 - total
        net_spread = gross_spread - fee
        if net_spread < config.min_net_spread:
            continue

        yes_side = pair.sides.get((venue, "YES"))
        no_side = pair.sides.get((venue, "NO"))
        # Update with REAL VWAP prices
        if yes_side:
            yes_side.best_ask = yes_vwap
        if no_side:
            no_side.best_ask = no_vwap

        opp = _build_opportunity(pair, "intra", venue, venue,
                                 yes_side, no_side, total, gross_spread, net_spread)
        opportunities.append(opp)
        logger.info(
            f"[ARB-REAL] {pair.event_title[:40]} | "
            f"YES@${yes_vwap:.3f} + NO@${no_vwap:.3f} = ${total:.3f} (net {net_spread*100:.2f}%)"
        )

        # Strategy 2+: Cross-venue arbitrage
        if len(config.active_venues) >= 2:
            result = pair.get_best_cross_spread()
            if result:
                total, yes_venue, no_venue = result
                if yes_venue != no_venue:
                    yes_fee = _get_venue_fee(yes_venue)
                    no_fee = _get_venue_fee(no_venue)
                    gross_spread = 1.0 - total
                    net_spread = gross_spread - yes_fee - no_fee
                    if net_spread >= config.min_net_spread:
                        dk = _dedup_key(key, "cross", yes_venue, no_venue)
                        if _is_duplicate(dk):
                            continue
                        yes_side = pair.sides.get((yes_venue, "YES"))
                        no_side = pair.sides.get((no_venue, "NO"))
                        strategy = "cross_chain" if {yes_venue, no_venue} == {"polymarket", "jupiter"} else "cross_platform"
                        opp = _build_opportunity(pair, strategy, yes_venue, no_venue,
                                                 yes_side, no_side, total, gross_spread, net_spread)
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
