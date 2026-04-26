# ArberAdeis — Multi-Venue Prediction Market Arbitrage Bot

## Overview
Automated arbitrage bot scanning Polymarket (Polygon), Jupiter Prediction (Solana), and Kalshi for pricing inefficiencies.

## Tech Stack
- **Backend**: Python 3.12 + FastAPI + py-clob-client + httpx
- **Data**: SQLite
- **Infra**: Docker Compose

## Commands
```bash
make up-build    # Build and start
make down        # Stop
make logs        # Tail logs
make dev         # Run locally
```

## Architecture
- `scanner.py` — Polls all venues every 500ms for spread opportunities
- `polymarket_api.py` — Polygon CLOB client (from polybot)
- `jupiter_api.py` — Jupiter Prediction REST client
- `evaluator.py` — Spread calculation after fees (TODO)
- `executor.py` — Dual-leg order execution (TODO)
- `database.py` — SQLite opportunity/leg logging
- `telegram.py` — Trade alerts

## Dashboard
Port 8020: http://localhost:8020

## Key Credentials
Same Polymarket wallet as polybot. See `.env`.
