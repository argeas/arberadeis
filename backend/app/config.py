"""Configuration for ArberAdeis — multi-venue arbitrage bot.
Runtime settings are persisted to data/runtime_config.json so they survive restarts."""

import json
from pathlib import Path
from pydantic_settings import BaseSettings

RUNTIME_CONFIG_PATH = Path("data/runtime_config.json")

# Fields that get persisted to runtime config (not secrets)
RUNTIME_FIELDS = [
    "venue_polymarket_enabled", "venue_jupiter_enabled", "venue_kalshi_enabled",
    "max_position_size", "min_net_spread", "daily_loss_limit", "orphan_daily_budget",
    "paper_mode", "poly_fee", "jupiter_fee", "kalshi_fee", "scan_interval_ms",
]


class Config(BaseSettings):
    # === Venue Toggles ===
    venue_polymarket_enabled: bool = True
    venue_jupiter_enabled: bool = False
    venue_kalshi_enabled: bool = False

    # === Polymarket (Polygon) ===
    poly_api_key: str = ""
    poly_api_secret: str = ""
    poly_api_passphrase: str = ""
    poly_private_key: str = ""
    poly_wallet_address: str = ""
    poly_proxy_address: str = ""
    builder_api_key: str = ""
    builder_api_secret: str = ""
    builder_api_passphrase: str = ""
    relayer_api_key: str = ""

    # === Jupiter (Solana) ===
    solana_private_key: str = ""
    solana_rpc_url: str = "https://api.mainnet-beta.solana.com"
    jito_block_engine_url: str = ""
    jupiter_api_key: str = ""

    # === Kalshi ===
    kalshi_api_key: str = ""
    kalshi_api_secret: str = ""

    # === Telegram ===
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # === Risk ===
    max_position_size: float = 50.0
    min_net_spread: float = 0.015
    daily_loss_limit: float = 100.0
    orphan_daily_budget: float = 50.0
    paper_mode: bool = True

    # === Fees (per side) ===
    poly_fee: float = 0.01
    jupiter_fee: float = 0.008
    kalshi_fee: float = 0.02

    # === Scanning ===
    scan_interval_ms: int = 500

    class Config:
        env_file = ".env"
        env_file_encoding = "utf-8"

    @property
    def active_venues(self) -> list[str]:
        venues = []
        if self.venue_polymarket_enabled:
            venues.append("polymarket")
        if self.venue_jupiter_enabled:
            venues.append("jupiter")
        if self.venue_kalshi_enabled:
            venues.append("kalshi")
        return venues

    def save_runtime(self):
        """Persist runtime settings to JSON file."""
        RUNTIME_CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
        data = {f: getattr(self, f) for f in RUNTIME_FIELDS}
        RUNTIME_CONFIG_PATH.write_text(json.dumps(data, indent=2))

    def load_runtime(self):
        """Load runtime settings from JSON file if it exists."""
        if RUNTIME_CONFIG_PATH.exists():
            try:
                data = json.loads(RUNTIME_CONFIG_PATH.read_text())
                for f, v in data.items():
                    if f in RUNTIME_FIELDS and hasattr(self, f):
                        setattr(self, f, v)
            except Exception:
                pass


config = Config()
config.load_runtime()
