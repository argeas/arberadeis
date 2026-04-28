"""Data models for ArberAdeis."""

from dataclasses import dataclass, field
from enum import Enum


class Venue(str, Enum):
    POLYMARKET = "polymarket"
    JUPITER = "jupiter"
    KALSHI = "kalshi"


class Strategy(str, Enum):
    INTRA = "intra"          # YES + NO < $1 on same venue
    CROSS_CHAIN = "cross_chain"  # Polymarket ↔ Jupiter
    CROSS_PLATFORM = "cross_platform"  # Any ↔ Kalshi
    THREE_WAY = "three_way"  # Best YES + best NO across all 3


class LegStatus(str, Enum):
    PENDING = "pending"
    FILLED = "filled"
    FAILED = "failed"
    ORPHAN = "orphan"


@dataclass
class MarketSide:
    """One side (YES or NO) of a market on a specific venue."""
    venue: str
    market_id: str     # venue-specific market identifier
    token_id: str      # venue-specific token identifier
    side: str          # "YES" or "NO"
    best_bid: float = 0.0
    best_ask: float = 0.0
    depth: float = 0.0  # USD liquidity at best ask
    last_update: float = 0.0


@dataclass
class MarketPair:
    """A matched market across venues — both YES and NO sides."""
    event_title: str
    poly_condition_id: str | None = None
    jup_market_id: str | None = None
    kalshi_market_id: str | None = None
    # Sides indexed by (venue, side)
    sides: dict[tuple[str, str], MarketSide] = field(default_factory=dict)

    def get_best_yes_ask(self) -> tuple[str, float]:
        """Return (venue, price) of cheapest YES ask across all venues."""
        best_venue, best_price = "", 999.0
        for (venue, side), ms in self.sides.items():
            if side == "YES" and ms.best_ask > 0 and ms.best_ask < best_price:
                best_venue, best_price = venue, ms.best_ask
        return best_venue, best_price

    def get_best_no_ask(self) -> tuple[str, float]:
        """Return (venue, price) of cheapest NO ask across all venues."""
        best_venue, best_price = "", 999.0
        for (venue, side), ms in self.sides.items():
            if side == "NO" and ms.best_ask > 0 and ms.best_ask < best_price:
                best_venue, best_price = venue, ms.best_ask
        return best_venue, best_price

    def get_intra_spread(self, venue: str) -> float | None:
        """Get YES+NO total cost on a single venue. <1.0 = arb opportunity."""
        yes = self.sides.get((venue, "YES"))
        no = self.sides.get((venue, "NO"))
        if yes and no and yes.best_ask > 0 and no.best_ask > 0:
            return yes.best_ask + no.best_ask
        return None

    def get_best_cross_spread(self) -> tuple[float, str, str] | None:
        """Find cheapest YES + cheapest NO across all venues. Returns (total, yes_venue, no_venue)."""
        yes_venue, yes_price = self.get_best_yes_ask()
        no_venue, no_price = self.get_best_no_ask()
        if yes_venue and no_venue and yes_price < 1.0 and no_price < 1.0:
            return (yes_price + no_price, yes_venue, no_venue)
        return None


@dataclass
class Opportunity:
    """A detected arbitrage opportunity."""
    id: int = 0
    timestamp: str = ""
    strategy: str = ""
    event_title: str = ""
    poly_condition_id: str | None = None
    jup_market_id: str | None = None
    kalshi_market_id: str | None = None
    yes_venue: str = ""
    no_venue: str = ""
    yes_price: float = 0.0
    no_price: float = 0.0
    total_cost: float = 0.0
    gross_spread: float = 0.0
    net_spread: float = 0.0
    yes_liquidity: float = 0.0
    no_liquidity: float = 0.0
    yes_token_id: str = ""
    no_token_id: str = ""
    status: str = "detected"
    skip_reason: str | None = None
    execution_time_ms: int | None = None


@dataclass
class ArbLeg:
    """One leg of an arbitrage trade."""
    id: int = 0
    opportunity_id: int = 0
    timestamp: str = ""
    leg: int = 1
    venue: str = ""
    chain: str = ""
    side: str = ""
    token_id: str = ""
    price: float = 0.0
    size: float = 0.0
    order_id: str | None = None
    tx_hash: str | None = None
    status: str = "pending"
    fill_price: float | None = None
    fees: float = 0.0
    pnl: float | None = None
    jito_bundle_id: str | None = None


@dataclass
class PortfolioState:
    """Current portfolio state across all venues."""
    poly_balance: float = 0.0
    jup_balance: float = 0.0
    kalshi_balance: float = 0.0
    daily_pnl: float = 0.0
    total_pnl: float = 0.0
    open_positions: int = 0
    orphan_count: int = 0
    halted: bool = False
    halt_reason: str = ""

    @property
    def total_balance(self) -> float:
        return self.poly_balance + self.jup_balance + self.kalshi_balance
