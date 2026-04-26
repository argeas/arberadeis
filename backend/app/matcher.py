"""Cross-venue market matching — finds equivalent events across platforms."""

import logging
import re

logger = logging.getLogger("arber")

# Known direct mappings for recurring markets
CURATED_MAPPINGS: dict[str, list[str]] = {
    # Polymarket slug patterns → Kalshi ticker patterns
    "fed-funds-rate": ["KXFED", "FEDFUNDS"],
    "bitcoin": ["KXBTC", "BTC"],
    "ethereum": ["KXETH", "ETH"],
    "presidential": ["KXPRES", "PRES"],
    "election": ["KXELEC"],
    "nfl": ["KXNFL"],
    "nba": ["KXNBA"],
    "mlb": ["KXMLB"],
    "inflation": ["KXCPI", "CPI"],
    "unemployment": ["KXJOBS"],
    "gdp": ["KXGDP"],
}


def normalize_title(title: str) -> str:
    """Normalize a market title for comparison."""
    t = title.lower().strip()
    # Remove common noise
    for remove in ["will ", "does ", "is ", "the ", "?", "!", ".", ",", "'s", "'", '"']:
        t = t.replace(remove, "")
    # Normalize whitespace
    t = re.sub(r"\s+", " ", t).strip()
    return t


def titles_match(title_a: str, title_b: str) -> float:
    """
    Score how likely two titles refer to the same event.
    Returns 0.0 (no match) to 1.0 (exact match).
    """
    if not title_a or not title_b:
        return 0.0

    a = normalize_title(title_a)
    b = normalize_title(title_b)

    # Exact match
    if a == b:
        return 1.0

    # One contains the other
    if len(a) > 15 and len(b) > 15:
        if a in b or b in a:
            return 0.9

    # Prefix match (first 40 chars)
    if len(a) > 30 and len(b) > 30:
        if a[:40] == b[:40]:
            return 0.85

    # Word overlap score
    words_a = set(a.split())
    words_b = set(b.split())
    if not words_a or not words_b:
        return 0.0

    overlap = words_a & words_b
    # Jaccard similarity
    jaccard = len(overlap) / len(words_a | words_b)

    # Boost if key entities match (numbers, proper nouns, dates)
    key_a = {w for w in words_a if any(c.isdigit() for c in w) or w[0:1].isupper()}
    key_b = {w for w in words_b if any(c.isdigit() for c in w) or w[0:1].isupper()}
    if key_a and key_b:
        key_overlap = len(key_a & key_b) / max(len(key_a), len(key_b))
        jaccard = max(jaccard, key_overlap)

    return jaccard


def match_poly_to_kalshi(poly_title: str, kalshi_markets: list[dict], threshold: float = 0.6) -> dict | None:
    """
    Find the best matching Kalshi market for a Polymarket event title.
    Returns the Kalshi market dict or None.
    """
    best_match = None
    best_score = 0.0

    for km in kalshi_markets:
        kalshi_title = km.get("title", "") or km.get("subtitle", "")
        score = titles_match(poly_title, kalshi_title)

        # Check curated mappings for boost
        for pattern, kalshi_patterns in CURATED_MAPPINGS.items():
            if pattern in poly_title.lower():
                ticker = km.get("ticker", "")
                if any(kp in ticker.upper() for kp in kalshi_patterns):
                    score = max(score, 0.8)

        if score > best_score and score >= threshold:
            best_score = score
            best_match = km

    if best_match:
        logger.debug(
            f"[MATCH] Poly '{poly_title[:40]}' → Kalshi '{best_match.get('title', '')[:40]}' (score={best_score:.2f})"
        )
    return best_match
