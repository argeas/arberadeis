# ArberAdeis — Feature Tracker

## Core Features

| Feature | Status | Phase | Notes |
|---------|--------|-------|-------|
| Config with venue toggles | ✅ Done | 1 | Polymarket, Jupiter, Kalshi toggles in .env + API |
| Polymarket scanner (45K+ markets) | ✅ Done | 1 | Gamma API market discovery + CLOB orderbook |
| Jupiter API client | ✅ Done | 1 | Events, degen, orderbook, orders |
| Intra-platform spread detection | ✅ Done | 1 | YES + NO < $1.00 on same venue |
| Cross-venue spread detection | ✅ Done | 1 | Best YES + best NO across venues |
| SQLite trade logging | ✅ Done | 1 | Opportunities, legs, orphans, portfolio tables |
| Telegram alerts | ✅ Done | 1 | Opportunities, executions, orphans, halts, startup |
| Web dashboard (port 8020) | ✅ Done | 1 | FastAPI + static HTML with live opportunity feed |
| Docker + Makefile | ✅ Done | 1 | docker-compose.yml, make up-build/down/logs |
| Paper/Live mode toggle | ✅ Done | 2 | Button in UI, confirmation for live, Telegram alert |
| Separate paper/live DBs | ✅ Done | 2 | arb_paper.db and arb_live.db, auto-switches |
| Execution engine | ✅ Done | 2 | Dual-leg with speed-ordered venue execution |
| Orphan handling | ✅ Done | 2 | Detects failed leg 2, attempts market exit |
| Venue toggles in UI | ✅ Done | 2 | Clickable badges to enable/disable venues |
| Daily loss limit | ✅ Done | 2 | Halts trading if exceeded |
| Orphan budget | ✅ Done | 2 | $50/day max orphan losses |
| Jupiter venue activation | ✅ Done | 3 | Full order flow: POST /orders → sign → submit to RPC |
| Jupiter degen market matching | ✅ Done | 3 | 5m/15m crypto events matched to Polymarket |
| Solana transaction signing | ✅ Done | 3 | solders library, base58/byte array key support |
| Jupiter order status polling | ✅ Done | 3 | Poll /orders/status for fill confirmation |
| Kalshi API integration | 📋 Planned | 4 | REST client + fuzzy market matching |
| Three-way arbitrage | 📋 Planned | 4 | Best YES + best NO across all 3 venues |
| Speed optimization (<100ms) | 📋 Planned | 5 | Sub-second detection → execution |
| Multi-outcome market support | 📋 Planned | 5 | Markets with 3+ outcomes |
| Historical spread analysis | 📋 Planned | 5 | Backtest optimal timing |
| Kelly position sizing | 📋 Planned | 5 | Adaptive sizing based on fill rates |
| Cloudflare tunnel route | 📋 Planned | - | Remote access via arber.oikodomeo.net |

## Dashboard Features

| Feature | Status | Notes |
|---------|--------|-------|
| Markets tracked count | ✅ Done | Live count of scanned markets |
| Opportunity feed table | ✅ Done | Recent opportunities with strategy, prices, spread |
| Venue badges (clickable) | ✅ Done | Toggle venues on/off from UI |
| Paper/Live toggle button | ✅ Done | Orange = paper, Red = live |
| Uptime counter | ✅ Done | Shows hours/minutes since start |
| P&L display | ✅ Done | Total P&L from executed arbs |
| Orphan counter | ✅ Done | Open orphan positions |
| Execution time display | 📋 Planned | Show ms per executed arb |
| Position tracker | 📋 Planned | Open positions awaiting resolution |
| Venue-specific P&L breakdown | 📋 Planned | P&L per venue |
| WebSocket live updates | 📋 Planned | Real-time opportunity stream |

## Risk Management

| Feature | Status | Notes |
|---------|--------|-------|
| Max position size | ✅ Done | $50 default, configurable |
| Min net spread threshold | ✅ Done | 1.5% default |
| Daily loss limit halt | ✅ Done | $100 default |
| Orphan daily budget | ✅ Done | $50 default |
| Leg risk timeout | 📋 Planned | Max 3s between legs |
| Auto orphan exit | 🔧 Partial | Paper mode simulates, live TODO |
| Telegram on every threshold | ✅ Done | Halt, orphan, execution alerts |
