"""Configuration for ArberAdeis — multi-venue arbitrage bot."""

from pydantic_settings import BaseSettings


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
    jupiter_api_key: str = ""  # From developers.jup.ag/portal

    # === Kalshi ===
    kalshi_api_key: str = ""
    kalshi_api_secret: str = ""

    # === Telegram ===
    telegram_bot_token: str = ""
    telegram_chat_id: str = ""

    # === Risk ===
    max_position_size: float = 50.0
    min_net_spread: float = 0.015  # 1.5%
    daily_loss_limit: float = 100.0
    orphan_daily_budget: float = 50.0
    paper_mode: bool = True

    # === Fees (per side) ===
    poly_fee: float = 0.01  # 1%
    jupiter_fee: float = 0.008  # 0.8%
    kalshi_fee: float = 0.02  # 2%

    # === Scanning ===
    scan_interval_ms: int = 500  # milliseconds between scans

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


config = Config()
