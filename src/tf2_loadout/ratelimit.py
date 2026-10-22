"""Per-client request limits and the cross-origin check for unsafe methods.

State is in-process: production is one Fly machine, so a dict per limiter is the
whole store, and it resetting on a suspend or deploy only hands out one more burst.

Clients are keyed on ``Fly-Client-IP``, never on ``request.client``. ``main()`` runs
uvicorn with ``forwarded_allow_ips="*"``, and uvicorn then reports the *leftmost*
``X-Forwarded-For`` entry -- which is whatever the client sent, since Fly appends to
that header rather than replacing it. Fly does overwrite ``Fly-Client-IP`` with the
address it saw, so that header is the only one a client can't pick for itself.

Rates are token buckets: a burst for a normal sitting, then a slow steady refill,
so a player asking a few questions in a row never notices and a script looping the
endpoint gets one request per refill interval.
"""

from __future__ import annotations

import ipaddress
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field

from fastapi.responses import JSONResponse
from starlette.datastructures import Headers
from starlette.types import ASGIApp, Receive, Scope, Send

CHAT_BURST = 10  # per IP; /chat and /chat/stream share one bucket
CHAT_REFILL_SECONDS = 30.0
LORE_BURST = 20  # per IP
LORE_REFILL_SECONDS = 3.0
REFRESH_BURST = 3  # per SteamID
REFRESH_REFILL_SECONDS = 60.0
CHAT_MAX_CONCURRENT_PER_IP = 2
# Well under fly.toml's hard_limit of 25 requests, so a handful of IPs holding long
# streams can't lock out everyone else's catalog and loadout requests.
CHAT_MAX_CONCURRENT_TOTAL = 12
# Bounds memory under IP rotation; evicting a key only hands it a full bucket.
DEFAULT_MAX_KEYS = 10_000
FLY_CLIENT_IP_HEADER = "fly-client-ip"


class RateLimiter:
    """A token bucket per key, least-recently-used keys evicted past ``max_keys``.

    ``hit`` never awaits, so on one event loop it needs no lock.
    """

    def __init__(
        self,
        burst: int,
        refill_seconds: float,
        *,
        max_keys: int = DEFAULT_MAX_KEYS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.burst = burst
        self.refill_seconds = refill_seconds
        self.max_keys = max_keys
        self._clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}

    def hit(self, key: str) -> float | None:
        """Spend a token for ``key``: ``None`` if allowed, else seconds until one is."""
        now = self._clock()
        # pop then re-insert, so dict order is least- to most-recently used.
        tokens, updated_at = self._buckets.pop(key, (float(self.burst), now))
        tokens = min(float(self.burst), tokens + (now - updated_at) / self.refill_seconds)
        wait = None
        if tokens >= 1:
            tokens -= 1
        else:
            wait = (1 - tokens) * self.refill_seconds
        self._buckets[key] = (tokens, now)
        while len(self._buckets) > self.max_keys:
            del self._buckets[next(iter(self._buckets))]
        return wait


class ConcurrencyLimiter:
    """Counts in-flight work per key, capped per key and in total."""

    def __init__(self, per_key: int, total: int) -> None:
        self.per_key = per_key
        self.total = total
        self._counts: dict[str, int] = {}
        self._in_flight = 0

    def try_acquire(self, key: str) -> bool:
        count = self._counts.get(key, 0)
        if count >= self.per_key or self._in_flight >= self.total:
            return False
        self._counts[key] = count + 1
        self._in_flight += 1
        return True

    def release(self, key: str) -> None:
        count = self._counts.get(key, 0) - 1
        if count > 0:
            self._counts[key] = count
        else:
            self._counts.pop(key, None)
        self._in_flight -= 1


def client_key(scope: Scope, trusted_header: str | None) -> str:
    """The limiter key for a request's client: its IP, or its IPv6 /64.

    ``trusted_header`` is read only when the caller knows the proxy sets it (on Fly);
    otherwise the socket peer. When it is trusted but missing, the request shares one
    "unknown" key instead of using the peer, which behind the proxy is the forgeable
    leftmost X-Forwarded-For entry. One host usually controls a whole IPv6 /64, so
    keying per address there would let it rotate through fresh buckets for free.
    """
    if trusted_header:
        value = (Headers(scope=scope).get(trusted_header) or "").split(",")[0].strip()
    else:
        client = scope.get("client")
        value = client[0] if client else ""
    value = value or "unknown"
    try:
        ip = ipaddress.ip_address(value)
    except ValueError:
        return value
    if ip.version == 6:
        # A mapped IPv4 address would otherwise collapse into ::/64 with every other.
        if ip.ipv4_mapped:
            return str(ip.ipv4_mapped)
        return str(ipaddress.ip_network(f"{value}/64", strict=False))
    return str(ip)


@dataclass(frozen=True)
class RateLimits:
    chat: RateLimiter = field(
        default_factory=lambda: RateLimiter(CHAT_BURST, CHAT_REFILL_SECONDS)
    )
    lore: RateLimiter = field(
        default_factory=lambda: RateLimiter(LORE_BURST, LORE_REFILL_SECONDS)
    )
    inventory_refresh: RateLimiter = field(
        default_factory=lambda: RateLimiter(REFRESH_BURST, REFRESH_REFILL_SECONDS)
    )
    chat_concurrency: ConcurrencyLimiter = field(
        default_factory=lambda: ConcurrencyLimiter(
            CHAT_MAX_CONCURRENT_PER_IP, CHAT_MAX_CONCURRENT_TOTAL
        )
    )


CHAT_PATHS = frozenset({"/chat", "/chat/stream"})


class ChatConcurrencyLimit:
    """429 a chat turn when its client (or the whole site) has too many in flight.

    Middleware rather than a check in the handler: the slot has to live exactly as
    long as the response. uvicorn speaks ASGI 2.3, so Starlette's StreamingResponse
    watches for disconnect and the app call returns once the stream ends or is
    cancelled -- the ``finally`` below always runs, even for a stream whose body
    never started, where a ``finally`` in the handler's generator would not.
    """

    def __init__(
        self, app: ASGIApp, limiter: ConcurrencyLimiter, client_ip_header: str | None = None
    ) -> None:
        self.app = app
        self.limiter = limiter
        self.client_ip_header = client_ip_header

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if not (
            scope["type"] == "http"
            and scope["method"] == "POST"
            and scope["path"] in CHAT_PATHS
        ):
            await self.app(scope, receive, send)
            return
        key = client_key(scope, self.client_ip_header)
        if not self.limiter.try_acquire(key):
            response = JSONResponse({"detail": "too many concurrent chat turns"}, status_code=429)
            await response(scope, receive, send)
            return
        try:
            await self.app(scope, receive, send)
        finally:
            self.limiter.release(key)


UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class OriginCheck:
    """403 an unsafe request whose ``Origin`` is neither this site nor allowlisted.

    The session cookie is ``SameSite=Lax``, and "site" there means the registrable
    domain: every ``*.yusufaf.dev`` subdomain (``analytics.`` included) counts as
    same-site and gets the cookie on a POST. A browser always sends ``Origin`` on
    such a request, so checking it closes that gap. A request with no ``Origin``
    passes -- it isn't a browser, and so isn't riding anyone else's cookie --
    while ``Origin: null`` (sandboxed frames, some redirects) is refused.
    """

    def __init__(self, app: ASGIApp, allowed_origins: Iterable[str] = ()) -> None:
        self.app = app
        self.allowed_origins = frozenset(origin.lower() for origin in allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["method"] in UNSAFE_METHODS:
            headers = Headers(scope=scope)
            origin = headers.get("origin")
            if origin is not None:
                origin = origin.lower()
                own = f"{scope['scheme']}://{headers.get('host', '').lower()}"
                if origin != own and origin not in self.allowed_origins:
                    response = JSONResponse(
                        {"detail": "cross-origin request refused"}, status_code=403
                    )
                    await response(scope, receive, send)
                    return
        await self.app(scope, receive, send)
