# Consolidated price estimates for portfolio connectors
ESTIMATED_PRICES = {
    "BTC": 65000.0,
    "ETH": 3500.0,
    "ZEC": 225.0,
    "SOL": 550.0,
}


def get_estimated_price(currency: str) -> float:
    return ESTIMATED_PRICES.get(currency, 100.0)
