# ArberAdeis — Multi-Venue Prediction Market Arbitrage Bot

## Overview

Automated arbitrage bot that detects and exploits pricing inefficiencies across **three prediction market venues**. The bot never predicts outcomes — it locks in mathematically guaranteed profit when the combined cost of covering all outcomes across venues is less than the payout.

### Venues
1. **Polymarket** — Polygon PoS, CLOB orderbook, largest liquidity
2. **Jupiter Prediction** — Solana, mirrors Polymarket markets, Jito bundles possible
3. **Kalshi** — Centralized exchange, REST API, US-regulated

### Strategies
1. **Intra-platform** — YES + NO on same venue costs < $1.00
2. **Cross-chain (Polymarket ↔ Jupiter)** — Same market, different prices on Polygon vs Solana
3. **Cross-platform (any venue ↔ Kalshi)** — Different platforms, same event
4. **Three-way** — Find cheapest YES and cheapest NO across all three venues
5. **Long-tail scanning** — Niche markets with 4-6% spreads sitting idle

---

## Architecture

```
┌────────────────────────────────────────────────────────────────┐
│                        ArberAdeis                              │
├───────────┬────────────┬────────────┬─────────────────────────┤
│ Scanners  │ Evaluator  │ Executor   │ Dashboard               │
│           │            │            │                         │
│ Polymarket│ Calculate  │ Dual-leg   │ FastAPI + Next.js       │
│ Jupiter   │ net spread │ execution  │ Real-time opportunity   │
│ Kalshi    │ after fees │ with       │ feed, P&L, positions    │
│           │            │ rollback   │                         │
│ 250ms     │ Kelly      │            │ WebSocket live updates  │
│ polling   │ sizing     │ Jito for   │                         │
│           │            │ Solana leg │                         │
└───────────┴────────────┴────────────┴─────────────────────────┘
      │            │            │                │
      ▼            ▼            ▼                ▼
 ┌─────────┐ ┌─────────┐ ┌──────────┐    ┌──────────┐
 │Polymarket│ │Jupiter  │ │ Kalshi   │    │ SQLite   │
 │ Polygon  │ │ Solana  │ │ REST API │    │+Telegram │
 │ CLOB API │ │Pred API │ │          │    │          │
 └─────────┘ └─────────┘ └──────────┘    └──────────┘
```

---

## Venue Details

### Polymarket (Polygon)
- **Chain:** Polygon PoS (Chain ID 137)
- **Settlement:** ~2 seconds, <$0.01 gas
- **API:** CLOB REST + Gamma API for market discovery
- **Auth:** Private key → derived API creds + Builder keys
- **Order types:** GTC, GTD, FOK
- **Fees:** ~1% per side
- **Existing code:** Full implementation in polybot (`py-clob-client`)

### Jupiter Prediction (Solana)
- **Chain:** Solana (~400ms finality)
- **API:** `https://prediction-market-api.jup.ag/api/v1/`
- **Key endpoints:**
  - `GET /events` — list active events
  - `GET /events/degen` — live crypto events (5m, 15m — same as Polymarket!)
  - `GET /events/degen/{symbol}` — current live degen event for BTC, ETH, SOL
  - `GET /orderbook/{marketId}` — orderbook data
  - `POST /orders` — create order transaction
  - `GET /positions` — list open positions
  - `POST /positions/{id}/claim` — claim winning positions
  - `POST /execute` — submit signed transactions
- **Auth:** Solana wallet keypair
- **Min trade:** $5 (raised April 14, 2026)
- **Jito bundles:** YES — Solana-native, can bundle order execution
- **Market matching:** Direct — Jupiter mirrors Polymarket markets by event ID

### Kalshi (Centralized)
- **API:** REST API with API key auth
- **Settlement:** Instant (centralized)
- **Auth:** API key from Kalshi dashboard
- **Fees:** Variable, typically 1-3%
- **Market matching:** Fuzzy text matching needed (different event naming)

---

## Strategy 1: Intra-Platform Arbitrage

### How it works
Every binary market has YES and NO. At resolution, one pays $1.00. If YES_ask + NO_ask < $1.00 on the same venue, buy both.

### Detection
```python
for market in all_active_markets:
    yes_ask = get_best_ask(market.yes_token)
    no_ask = get_best_ask(market.no_token)
    total = yes_ask + no_ask
    if total < 1.00 - FEES:
        execute(market, yes_ask, no_ask)
```

### Execution
- **Polymarket:** Buy YES + NO via CLOB in rapid succession (same Polygon block if possible)
- **Jupiter:** Bundle both orders in a Jito bundle → truly atomic on Solana

---

## Strategy 2: Cross-Chain Arbitrage (Polymarket ↔ Jupiter)

### Why this is the best opportunity
Jupiter mirrors Polymarket markets on Solana. Same events, same resolutions. But:
- **Different orderbooks** → different prices
- **Different chains** → price lag between them
- **Jupiter is newer** → less bot competition, wider spreads
- **Degen markets** → Jupiter has 5m/15m crypto events (same as our polybot markets!)

### Example
```
Polymarket (Polygon): BTC 5m Up YES = $0.62
Jupiter (Solana):     BTC 5m Up NO  = $0.34
Total: $0.96 → Payout: $1.00 → Profit: $0.04 (4.2%)
```

### Execution
1. **Detect:** Scanner finds price divergence between Poly and Jupiter for same event
2. **Evaluate:** Calculate net spread after fees on both platforms
3. **Execute leg 1 (Jupiter/Solana):** Place order via Jito bundle (atomic, ~400ms)
4. **Execute leg 2 (Polymarket/Polygon):** Place FOK order via CLOB (~2s)
5. **Monitor:** Track both fills, handle orphans if leg 2 fails

### Market Matching
Jupiter mirrors Polymarket events directly — matching is trivial:
- `GET /events/degen/BTC` on Jupiter = `btc-updown-5m` on Polymarket
- Same resolution oracle, same outcomes
- No fuzzy matching needed for degen/crypto markets

---

## Strategy 3: Cross-Platform (↔ Kalshi)

### Detection
```python
for poly_market in polymarket_markets:
    kalshi_match = fuzzy_match(poly_market, kalshi_markets)
    if kalshi_match:
        poly_yes = get_poly_ask(market.yes_token)
        kalshi_no = get_kalshi_ask(match, "NO")
        if poly_yes + kalshi_no < 1.00 - FEES:
            execute_cross(poly_market, kalshi_match)
```

### Market Matching (Kalshi only)
- Fuzzy text similarity on event titles
- Curated mapping table for recurring markets (elections, Fed meetings)
- Resolution source matching as verification

---

## Strategy 4: Three-Way Arbitrage

### Find cheapest YES and cheapest NO across all three venues
```python
for event in matched_events:
    yes_prices = {
        "poly": get_poly_ask(event, "YES"),
        "jup": get_jup_ask(event, "YES"),
        "kalshi": get_kalshi_ask(event, "YES"),
    }
    no_prices = {
        "poly": get_poly_ask(event, "NO"),
        "jup": get_jup_ask(event, "NO"),
        "kalshi": get_kalshi_ask(event, "NO"),
    }
    cheapest_yes = min(yes_prices, key=yes_prices.get)
    cheapest_no = min(no_prices, key=no_prices.get)
    total = yes_prices[cheapest_yes] + no_prices[cheapest_no]
    if total < 1.00 - TOTAL_FEES:
        execute(cheapest_yes_venue, cheapest_no_venue)
```

---

## Strategy 5: Long-Tail Scanning

- Top 5% of markets are arbitraged by HFT in seconds
- Remaining 95% (regional politics, mid-tier sports, niche macro) has 4-6% spreads idle for 15-30s
- Scan ALL active markets across all venues, not just popular ones
- Sort by: spread × liquidity
- Min liquidity: $5K (avoid manipulation)
- 60-70% of fills expected from long-tail

---

## Risk Management

### Per-Trade
- Max position: 8% of portfolio
- Min net spread: 1.5% (after all fees)
- Max single market exposure: $500
- Slippage protection: cancel if spread < 0.5% after order submission

### Kill Switches
- Daily loss limit: -5% → halt all trading
- Total drawdown: -15% → kill switch
- Orphan position limit: >3 unhedged → halt
- Telegram alert on every threshold

### Leg Risk (Cross-Chain)
- Max time between leg 1 and leg 2: 3 seconds
- If leg 2 fails: immediately market-exit leg 1
- Track orphans separately with dedicated P&L
- Orphan loss budget: max $50/day
- Prefer Jupiter first (faster finality) then Polymarket

### Fee Budget
| Venue | Fee per side | Round-trip |
|-------|-------------|------------|
| Polymarket | ~1% | ~2% |
| Jupiter | ~0.5-1% | ~1-2% |
| Kalshi | ~1-3% | ~2-6% |

**Min gross spread to be profitable:**
- Intra-platform: >2% (one venue's fees × 2 sides)
- Poly ↔ Jupiter: >3% (both venues' fees)
- Any ↔ Kalshi: >4% (Kalshi fees are higher)

---

## Technical Stack

### Reuse from Polybot
| Component | Source | Status |
|-----------|--------|--------|
| CLOB client init | `polybot/polymarket.py` | Direct reuse |
| Order placement | `polybot/polymarket.py` | Direct reuse |
| Market discovery (Gamma) | `polybot/polymarket.py` | Adapt for all markets |
| Trade database | `polybot/database.py` | Adapt schema |
| Telegram notifications | `polybot/telegram.py` | Direct reuse |
| WebSocket manager | `polybot/ws_manager.py` | Direct reuse |
| FastAPI dashboard | `polybot/main.py` | Adapt endpoints |
| Frontend UI | `polybot/frontend/` | Fork and modify |
| Config/auth | `polybot/config.py` | Extend for 3 venues |
| Wallet credentials | `polybot/.env` | Add Solana + Kalshi keys |

### New Components
| Component | Purpose |
|-----------|---------|
| `jupiter_api.py` | Jupiter Prediction REST client + Solana transaction signing |
| `kalshi_api.py` | Kalshi REST client |
| `scanner.py` | Multi-venue market scanner (polls all 3 every 250ms) |
| `evaluator.py` | Cross-venue spread calculation, fee deduction, sizing |
| `executor.py` | Dual-leg execution with Jito bundles for Solana |
| `matcher.py` | Event matching across venues (direct for Jupiter, fuzzy for Kalshi) |
| `risk.py` | Kill switches, position limits, orphan budget |
| `orphan_manager.py` | Track and exit single-leg positions |

### Dependencies
```
# Polymarket (existing)
py-clob-client==0.34.6
py-builder-relayer-client==0.0.1
web3>=6.0.0

# Jupiter / Solana
solana>=0.34.0              # Solana Python SDK
solders>=0.21.0             # Solana transaction building
anchorpy>=0.20.0            # Anchor program interaction
jito-sdk>=0.1.0             # Jito bundle submission (if available)
httpx>=0.28.0               # Jupiter REST API calls

# Kalshi
httpx>=0.28.0               # Kalshi REST API

# Infrastructure
fastapi>=0.115.0
uvicorn>=0.34.0
websockets>=14.1
aiosqlite>=0.20.0
pydantic>=2.10.0
pydantic-settings>=2.7.0
python-dotenv>=1.0.0
rich>=13.0.0
thefuzz>=0.22.0             # Fuzzy matching for Kalshi
```

---

## Database Schema

```sql
-- Detected opportunities
CREATE TABLE opportunities (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    strategy TEXT,              -- "intra", "cross_chain", "cross_platform", "three_way"
    event_title TEXT,
    poly_condition_id TEXT,
    jup_market_id TEXT,
    kalshi_market_id TEXT,
    yes_venue TEXT,             -- "polymarket", "jupiter", "kalshi"
    no_venue TEXT,
    yes_price REAL,
    no_price REAL,
    total_cost REAL,
    gross_spread REAL,
    net_spread REAL,
    yes_liquidity REAL,
    no_liquidity REAL,
    status TEXT,                -- "detected", "executed", "skipped", "failed"
    skip_reason TEXT,
    execution_time_ms INTEGER
);

-- Executed legs
CREATE TABLE arb_legs (
    id INTEGER PRIMARY KEY,
    opportunity_id INTEGER,
    timestamp TEXT,
    leg INTEGER,                -- 1 or 2
    venue TEXT,                 -- "polymarket", "jupiter", "kalshi"
    chain TEXT,                 -- "polygon", "solana", "centralized"
    side TEXT,                  -- "YES" or "NO"
    token_id TEXT,
    price REAL,
    size REAL,
    order_id TEXT,
    tx_hash TEXT,
    status TEXT,                -- "filled", "failed", "orphan"
    fill_price REAL,
    fees REAL,
    pnl REAL,
    jito_bundle_id TEXT         -- NULL if not Solana
);

-- Orphan positions
CREATE TABLE orphans (
    id INTEGER PRIMARY KEY,
    leg_id INTEGER,
    timestamp TEXT,
    venue TEXT,
    side TEXT,
    size REAL,
    entry_price REAL,
    exit_price REAL,
    exit_timestamp TEXT,
    pnl REAL,
    status TEXT                  -- "open", "exited", "resolved"
);

-- Portfolio snapshots
CREATE TABLE portfolio (
    id INTEGER PRIMARY KEY,
    timestamp TEXT,
    poly_balance REAL,
    jup_balance REAL,
    kalshi_balance REAL,
    total_value REAL,
    daily_pnl REAL,
    open_positions INTEGER,
    orphan_count INTEGER
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
│   │   ├── config.py            # Multi-venue settings
│   │   ├── scanner.py           # Multi-venue market poller
│   │   ├── evaluator.py         # Spread calc, fee deduction, sizing
│   │   ├── executor.py          # Dual-leg execution engine
│   │   ├── matcher.py           # Cross-venue event matching
│   │   ├── polymarket_api.py    # Polygon CLOB client (from polybot)
│   │   ├── jupiter_api.py       # Solana Jupiter Prediction client
│   │   ├── kalshi_api.py        # Kalshi REST client
│   │   ├── jito.py              # Jito bundle submission for Solana
│   │   ├── database.py          # SQLite (adapted)
│   │   ├── models.py            # Data models
│   │   ├── telegram.py          # Notifications (from polybot)
│   │   ├── ws_manager.py        # WebSocket (from polybot)
│   │   ├── risk.py              # Kill switches, limits
│   │   └── orphan_manager.py    # Single-leg position handling
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/                     # Fork from polybot
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

## Environment Variables

```env
# Polymarket (Polygon)
POLY_API_KEY=
POLY_API_SECRET=
POLY_API_PASSPHRASE=
POLY_PRIVATE_KEY=
POLY_WALLET_ADDRESS=
POLY_PROXY_ADDRESS=
BUILDER_API_KEY=
BUILDER_API_SECRET=
BUILDER_API_PASSPHRASE=

# Jupiter (Solana)
SOLANA_PRIVATE_KEY=            # Base58 or byte array
SOLANA_RPC_URL=                # Helius/QuickNode for speed
JITO_BLOCK_ENGINE_URL=         # For bundle submission

# Kalshi
KALSHI_API_KEY=
KALSHI_API_SECRET=

# Telegram
TELEGRAM_BOT_TOKEN=
TELEGRAM_CHAT_ID=

# Risk
MAX_POSITION_PCT=0.08
DAILY_LOSS_LIMIT_PCT=0.05
TOTAL_DRAWDOWN_KILL_PCT=0.15
MIN_NET_SPREAD=0.015
ORPHAN_DAILY_BUDGET=50
```

---

## Implementation Phases

### Phase 1: Intra-Polymarket Scanner (Week 1)
- Scan ALL active Polymarket markets via Gamma API
- Calculate YES + NO ask for each
- Detect spreads > 1.5% after fees
- Log opportunities to SQLite
- Paper trade mode
- Telegram alerts on opportunities
- Basic dashboard showing opportunity feed

### Phase 2: Jupiter Integration (Week 2)
- Jupiter Prediction API client
- Fetch degen/crypto events (5m, 15m — same as polybot!)
- Match Jupiter events to Polymarket events
- Detect cross-chain spreads
- Paper trade cross-chain opportunities

### Phase 3: Execution Engine (Week 3)
- Polymarket leg: FOK orders via CLOB
- Jupiter leg: Signed transactions via API
- Jito bundle submission for Solana leg (atomic)
- Orphan detection and auto-exit
- Risk limits enforcement
- Full dashboard with positions, P&L

### Phase 4: Kalshi + Three-Way (Week 4)
- Kalshi API client
- Fuzzy market matching
- Three-way spread detection
- Extended executor for 3 venues

### Phase 5: Optimization (Ongoing)
- Sub-100ms detection → execution latency
- Multi-outcome market support
- Historical spread analysis for timing
- Adaptive fee estimation
- Long-tail market prioritization

---

## Key Metrics

- Opportunities detected / hour (per strategy)
- Opportunities executed / hour
- Fill rate (both legs successful)
- Orphan rate and orphan P&L
- Average net spread captured
- Daily / weekly / monthly P&L
- Latency: detection → execution (ms)
- Venue-specific fill rates
- Jito bundle success rate (Solana)

---

## Important Notes

1. **Jito bundles work for Jupiter leg only** — Solana-native. Polymarket (Polygon) and Kalshi (centralized) cannot use Jito. Cross-chain is never truly atomic.

2. **Jupiter mirrors Polymarket** — Same events, same resolutions. The degen/crypto markets (5m, 15m) are identical to polybot's markets. Market matching is trivial for these.

3. **Fees eat the spread** — Polymarket ~1%/side, Jupiter ~0.5-1%/side, Kalshi ~1-3%/side. A 3% gross spread can become <1% net. Only trade when math works.

4. **Speed is the moat** — Other bots are scanning too. Sub-second scanning across all three venues is essential. First to detect and execute wins.

5. **Start with intra-platform** — Safest (no leg risk). Then cross-chain Poly↔Jupiter (best opportunity, direct market matching). Kalshi last (fuzzy matching adds complexity).

6. **Long-tail is where the money is** — Top markets are fought over by HFT. Niche markets have wider spreads and less competition.
