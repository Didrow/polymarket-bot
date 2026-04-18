import math

def clamp(x, min_val=0.01, max_val=0.99):
    return max(min_val, min(max_val, x))


def calibrated_prob(p: float) -> float:
    """
    Зменшує overconfidence моделі
    """
    return 0.5 + (p - 0.5) * 0.6


def is_liquid_market(market) -> bool:
    """
    Фільтр сміттєвих маркетів
    """
    try:
        if market.best_bid_yes == 0 and market.best_ask_yes == 0:
            return False

        spread = market.best_ask_yes - market.best_bid_yes

        if spread > 0.2:
            return False

        if getattr(market, "volume_usd", 0) < 5000:
            return False

        if getattr(market, "open_interest", 0) < 2000:
            return False

        return True
    except:
        return False


def calculate_edge(market, our_prob: float):
    """
    Головна функція
    """

    if market is None:
        return None

    if not is_liquid_market(market):
        return None

    if our_prob is None:
        return None

    # FIX 1: калібруємо probability
    our_prob = calibrated_prob(our_prob)

    # FIX 2: беремо midpoint
    market_prob = getattr(market, "midpoint_yes", None)

    if market_prob is None:
        return None

    # FIX 3: clamp щоб не було 0 або 1
    market_prob = clamp(market_prob)

    edge = our_prob - market_prob

    # FIX 4: анти-бред фільтр
    if abs(our_prob - 0.5) > 0.45:
        return None

    # мінімальний edge
    if abs(edge) < 0.03:
        return None

    return {
        "edge": edge,
        "our_prob": our_prob,
        "market_prob": market_prob
    }
