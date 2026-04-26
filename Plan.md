# ArberAdeis — Polymarket Arbitrage Bot

## Overview

Automated arbitrage bot that detects and exploits pricing inefficiencies across Polymarket markets. Two strategies:

1. **Intra-platform arbitrage** — When YES + NO on the same Polymarket market costs < $1.00
2. **Cross-platform arbitrage** — When Polymarket + Kalshi price the same event differently

The bot never predicts outcomes. It locks in mathematically guaranteed profit when the combined cost of covering all outcomes is less than the payout.

---

## Architecture

```
┌──────────────────────────────────────────────────────┐
│                    ArberAdeis                         │
├──────────┬───────────┬───────────┬───────────────────┤
│ Scanner  │ Evaluator │ Executor  │ Dashboard         │
│          │           │           │                   │
│ Polls    │ Scores    │ Places    │ FastAPI +         │
│ markets  │ spreads   │ orders    │ Next.js UI        │
│ every    │ checks    │ atomically│                   │
│ 250ms    │ fees/     │ on both   │ Real-time P&L,    │
│          │ slippage  │ legs      │ open positions,   │
│ Gamma    │           │           │ opportunity feed  │
│ API +    │ Kelly     │ CLOB API  │                   │
│ CLOB API │ sizing    │ + Kalshi  │ WebSocket live    │
│          │           │ API       │ updates           │
└──────────┴───────────┴───────────┴───────────────────┘
         │                  │                │
         ▼                  ▼                ▼
    ┌─────────┐      ┌──────────┐     ┌──────────┐
    │ Polygon │      │ Kalshi   │     │ SQLite   │
    │ (CLOB)  │      │ REST API │     │ + Telegram│
    └─────────┘      └──────────┘     └──────────┘
```

---

## Strategy 1: Intra-Polymarket Arbitrage

### How it works
Every Polymarket binary market has YES and NO tokens. At resolution, one pays $1.00 and the other pays $0.00. If you can buy YES at $0.55 and NO at $0.42 (total $0.97), you're guaranteed $1.00 back — a 3.1% risk-free return.

### Where the edge comes from
- **Stale orders** — Limit orders sitting on the book that haven't been updated
- **Multi-outcome markets** — Markets with 3+ outcomes where the sum of all YES prices < $1.00
- **Market maker spread** — When bid-ask spreads create momentary mispricings
- **Resolution approaching** — Rapid price movement near resolution leaves orphaned orders

### Detection
```python
for market in all_active_markets:
    yes_ask = get_best_ask(market.yes_token_id)
    no_ask = get_best_ask(market.no_token_id)
    total_cost = yes_ask + no_ask
    if total_cost < 1.00:
        spread = 1.00 - total_cost
        if spread > MIN_SPREAD + FEES:
            execute_arb(market, yes_ask, no_ask)
```

### Execution
- Buy YES at ask + buy NO at ask in same Polygon block if possible
- Use CTF Exchange for atomic settlement
- Net cost < $1.00, guaranteed payout = $1.00

---

## Strategy 2: Cross-Platform Arbitrage (Polymarket ↔ Kalshi)

### How it works
Same event priced differently on two platforms:
- Polymarket: "Fed cuts rates" YES = $0.61
- Kalshi: "Fed cuts rates" NO = $0.35
- Total cost: $0.61 + $0.35 = $0.96
- Guaranteed payout: $1.00
- Profit: $0.04 (4.2%)

### Market Matching
The hardest part — finding equivalent markets across platforms:
- Text similarity matching (fuzzy match event titles)
- Curated mapping table for known recurring markets
- Resolution source matching (same oracle = same outcome)

### Execution Risk (NO ATOMIC CROSS-PLATFORM)
- Polymarket runs on Polygon; Kalshi is centralized REST API
- **Jito bundles do NOT work here** (Jito = Solana only, Polymarket = Polygon)
- Two separate API calls, ~100-500ms apart
- Leg risk: one side fills, other doesn't
- Mitigation: speed (parallel execution), small sizes, wide spreads only

### Detection
```python
for poly_market in polymarket_markets:
    kalshi_match = find_matching_kalshi_market(poly_market)
    if kalshi_match:
        poly_yes = get_best_ask(poly_market.yes_token)
        kalshi_no = get_kalshi_ask(kalshi_match, "NO")
        total = poly_yes + kalshi_no
        if total < 1.00 - FEES:
            execute_cross_arb(poly_market, kalshi_match)
```

---

## Strategy 3: Long-Tail Market Scanning

### The 95% opportunity
Top markets (elections, BTC price) are arbitraged by HFT in seconds. But:
- Regional politics, mid-tier sports, niche macro
- 4-6% spreads sitting idle for 15-30 seconds
- Lower liquidity but also lower competition
- 60-70% of fills come from these markets

### Implementation
- Scan ALL active markets, not just popular ones
- Sort by spread size × liquidity
- Prioritize markets with $50K+ volume (fillable) but low bot activity
- Skip markets < $5K volume (manipulation risk)

---

## Risk Management

### Per-Trade Limits
- Max position: 8% of portfolio
- Min spread after fees: 1.5% (Polymarket ~1% fee per side)
- Max single market exposure: $500
- Slippage protection: cancel if spread < 0.5% after fees

### Kill Switches
- Daily loss limit: -5% → halt all trading
- Total drawdown: -15% → kill switch
- Orphan position limit: if >3 unhedged positions, halt
- Telegram alert on every threshold

### Leg Risk Management
- Maximum time between leg 1 and leg 2: 2 seconds
- If leg 2 fails: immediately try to exit leg 1 at market
- Track orphan positions separately
- Orphan loss budget: max $50/day

---

## Technical Stack

### Reuse from Polybot
| Component | Source | Status |
|-----------|--------|--------|
| CLOB client init | `polybot/backend/app/polymarket.py` | Direct reuse |
| Order placement | `polybot/backend/app/polymarket.py` | Direct reuse |
| Market discovery (Gamma API) | `polybot/backend/app/polymarket.py` | Adapt for all markets |
| Trade database | `polybot/backend/app/database.py` | Adapt schema |
| Telegram notifications | `polybot/backend/app/telegram.py` | Direct reuse |
| WebSocket manager | `polybot/backend/app/ws_manager.py` | Direct reuse |
| FastAPI dashboard | `polybot/backend/app/main.py` | Adapt endpoints |
| Frontend UI | `polybot/frontend/` | Fork and modify |
| Config/auth | `polybot/backend/app/config.py` | Direct reuse |

### New Components Needed
| Component | Purpose |
|-----------|---------|
| Market scanner | Poll ALL Polymarket markets for spread opportunities |
| Kalshi API client | REST client for Kalshi order placement |
| Market matcher | Fuzzy text matching for cross-platform event pairing |
| Spread evaluator | Calculate net spread after fees, slippage |
| Dual-leg executor | Parallel order placement with rollback |
| Orphan tracker | Monitor and manage single-leg positions |
| Opportunity feed | Real-time stream of detected opportunities |

### Dependencies
```
py-clob-client==0.34.6        # Polymarket CLOB
py-builder-relayer-client==0.0.1
fastapi>=0.115.0               # Dashboard backend
uvicorn>=0.34.0
websockets>=14.1
httpx>=0.28.0                  # Async HTTP (Kalshi API)
aiosqlite>=0.20.0              # Trade logging
pydantic>=2.10.0
pydantic-settings>=2.7.0
web3>=6.0.0                    # Polygon on-chain
rich>=13.0.0                   # Terminal dashboard
thefuzz>=0.22.0                # Fuzzy string matching (market matcher)
```

---

## Database Schema

```sql
-- Detected opportunities (whether traded or not)
CREATE TABLE opportunities (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    strategy TEXT,          -- "intra" or "cross"
    market_slug TEXT,
    poly_condition_id TEXT,
    kalshi_market_id TEXT,  -- NULL for intra
    yes_price REAL,
    no_price REAL,
    total_cost REAL,
    gross_spread REAL,      -- 1.00 - total_cost
    net_spread REAL,        -- after fees
    liquidity REAL,         -- min depth of both sides
    status TEXT,            -- "detected", "executed", "skipped", "failed"
    skip_reason TEXT
);

-- Executed arbitrage trades (two legs per arb)
CREATE TABLE arb_trades (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER,
    timestamp TEXT,
    leg INTEGER,            -- 1 or 2
    platform TEXT,          -- "polymarket" or "kalshi"
    side TEXT,              -- "YES" or "NO"
    token_id TEXT,
    price REAL,
    size REAL,
    order_id TEXT,
    status TEXT,            -- "filled", "failed", "orphan"
    fill_price REAL,
    pnl REAL
);

-- Orphan positions (single-leg fills)
CREATE TABLE orphans (
    id INTEGER PRIMARY KEY,
    arb_trade_id INTEGER,
    timestamp TEXT,
    platform TEXT,
    side TEXT,
    size REAL,
    entry_price REAL,
    exit_price REAL,
    exit_timestamp TEXT,
    pnl REAL,
    status TEXT              -- "open", "exited", "resolved"
);
```

---

## Project Structure

```
arberadeis/
├── backend/
│   ├── app/
│   │   ├── __init__.py
│   │   ├── main.py              # FastAPI + background tasks
│   │   ├── config.py            # Settings (from polybot)
│   │   ├── scanner.py           # Market scanner (all Polymarket markets)
│   │   ├── evaluator.py         # Spread calculation, fee deduction
│   │   ├── executor.py          # Dual-leg order execution
│   │   ├── matcher.py           # Cross-platform market matching
│   │   ├── polymarket_api.py    # CLOB client (from polybot)
│   │   ├── kalshi_api.py        # Kalshi REST client
│   │   ├── database.py          # SQLite (adapted from polybot)
│   │   ├── models.py            # Data models
│   │   ├── telegram.py          # Notifications (from polybot)
│   │   ├── ws_manager.py        # WebSocket (from polybot)
│   │   ├── risk.py              # Kill switches, position limits
│   │   └── orphan_manager.py    # Handle single-leg positions
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                     # Fork from polybot, adapt UI
│   └── ...
├── docker-compose.yml
├── Makefile
├── .env
├── .env.example
├── .gitignore
├── CLAUDE.md
└── Plan.md
```

---

## Implementation Phases

### Phase 1: Intra-Polymarket Scanner (Week 1)
- Scan ALL active markets via Gamma API
- Calculate YES + NO ask for each
- Detect spreads > 1.5% after fees
- Log opportunities to SQLite
- Paper trade mode (log but don't execute)
- Telegram alerts on opportunities

### Phase 2: Execution Engine (Week 2)
- Place both legs via CLOB API
- Atomic execution on Polygon where possible
- Orphan detection and exit logic
- Risk limits (position size, daily loss)
- Dashboard with opportunity feed

### Phase 3: Cross-Platform (Week 3-4)
- Kalshi API integration
- Market matching (fuzzy text + curated list)
- Parallel dual-leg execution
- Leg risk management
- Extended dashboard

### Phase 4: Optimization (Ongoing)
- Speed optimization (sub-100ms detection → execution)
- Multi-outcome market support
- Historical spread analysis
- Kelly sizing based on observed fill rates

---

## Key Metrics to Track

- Opportunities detected / hour
- Opportunities executed / hour
- Fill rate (both legs successful)
- Orphan rate
- Average spread captured
- Daily P&L
- Largest orphan loss
- Time from detection to execution (latency)

---

## Important Notes

1. **Jito bundles do NOT work for Polymarket** — Polymarket is on Polygon, not Solana. Atomic cross-platform execution is impossible. The post you referenced was incorrect about this.

2. **Fees matter** — Polymarket charges ~1% per side. A 3% gross spread becomes ~1% net. Only trade when net spread > 1.5%.

3. **Liquidity is king** — A 10% spread with $50 liquidity is useless. Prioritize spread × liquidity.

4. **Speed is the moat** — Other bots are scanning too. The first to detect and execute wins. Sub-second scanning is essential.

5. **Start with intra-platform** — Safer, simpler, no leg risk. Cross-platform adds complexity and risk.
