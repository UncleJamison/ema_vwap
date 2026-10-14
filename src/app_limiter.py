"""
Shared slowapi rate-limiter instance (ema_vwap-tq41).

Owned here so both src.app (which binds it to app.state.limiter and registers
the RateLimitExceeded handler) and the extracted route modules (which apply
@limiter.limit decorators) reference the exact same Limiter object.
"""

from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address)
