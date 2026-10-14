"""
Symbol validation module.
Provides validation functions used across the data pipeline to reject
malformed or non-existent symbols before optimization or trading.
"""

import re

from src.providers.base import BaseDataProvider, normalize_symbol

# Regex: base asset must start with a letter, followed by letters/digits;
# optional quote currency separated by /. Only uppercase letters/digits allowed.
SYMBOL_RE = re.compile(r"^[A-Z][A-Z0-9]*(?:/(?:USDT|USDC|USD|[A-Z0-9]+))?$")


def validate_symbol_format(symbol: str) -> str:
    """
    Validate normalized symbol format.

    Returns the normalized symbol if valid.
    Raises ValueError if the symbol is malformed or uses a non-standard base.
    """
    normalized = normalize_symbol(symbol).strip()
    if not SYMBOL_RE.match(normalized):
        raise ValueError(
            f"Invalid symbol format or non-standard base: {symbol!r} -> {normalized!r}. "
            "Base asset must start with a letter and contain only uppercase "
            "alphanumerics."
        )
    return normalized


def validate_symbol_against_provider(provider: BaseDataProvider, symbol: str) -> str:
    """
    Validate a symbol against a provider's supported symbols list (cached).

    Falls back to format validation if the provider has no supported_symbols registry.
    If the provider has a validate_symbol method, it is called first.
    """
    normalized = validate_symbol_format(symbol)
    # If provider has its own validation, use it
    if hasattr(provider, "validate_symbol"):
        return provider.validate_symbol(symbol)
    supported = getattr(provider, "SUPPORTED_SYMBOLS", None)
    if supported is None:
        return normalized  # Provider has no registry; format check only
    if normalized not in supported and normalize_symbol(symbol) not in supported:
        raise ValueError(
            f"Invalid symbol: {normalized!r} is not a valid symbol in provider "
            f"{provider.name!r} supported symbols"
        )
    return normalized
