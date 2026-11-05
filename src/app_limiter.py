"""
Shared slowapi rate-limiter instance (ema_vwap-tq41).

Owned here so both src.app (which binds it to app.state.limiter and registers
the RateLimitExceeded handler) and the extracted route modules (which apply
@limiter.limit decorators) reference the exact same Limiter object.
"""

import os

from slowapi import Limiter
from slowapi.util import get_remote_address

# Allow disabling rate limiting for load testing
DISABLE_RATE_LIMITING = (
    os.environ.get("DISABLE_RATE_LIMITING", "false").lower() == "true"
)

if DISABLE_RATE_LIMITING:
    # Use a no-op limiter that doesn't actually limit
    class NoOpLimiter:
        def limit(self, limit_value):
            def decorator(func):
                return func

            return decorator

        def __getattr__(self, name):
            return lambda *args, **kwargs: None

    limiter = NoOpLimiter()
else:
    limiter = Limiter(key_func=get_remote_address)
