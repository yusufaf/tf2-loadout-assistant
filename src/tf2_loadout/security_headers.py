"""Security headers on every response, chiefly a strict Content-Security-Policy.

Each player's LLM key sits in ``localStorage`` on a page that also loads a
third-party script (Umami). The CSP is what stops an injected script, or a script
pulled in from a host nobody allowlisted, from running there and reading the key, so
the policy allows no ``'unsafe-inline'``, no ``'unsafe-eval'`` and no wildcards:
only the exact hosts the built page, Umami's beacon, Steam's icon and avatar CDNs and
Google Fonts need.

Plain ASGI rather than ``BaseHTTPMiddleware`` so the /chat/stream body isn't routed
through it, same as ``CanonicalHostRedirect``. HSTS goes out only when the request
arrived over https: browsers ignore it on plain http (RFC 6797), and local dev is
plain http. Headers are added with ``setdefault``, so a route that needs a different
value can still set its own.
"""

from __future__ import annotations

from starlette.datastructures import MutableHeaders
from starlette.types import ASGIApp, Message, Receive, Scope, Send

ANALYTICS_ORIGIN = "https://analytics.yusufaf.dev"

CSP_DIRECTIVES: dict[str, tuple[str, ...]] = {
    "default-src": ("'self'",),
    "script-src": ("'self'", ANALYTICS_ORIGIN),
    "style-src": ("'self'", "https://fonts.googleapis.com"),
    "font-src": ("'self'", "https://fonts.gstatic.com"),
    "img-src": (
        "'self'",
        # Item icons from the schema feed.
        "https://media.steampowered.com",
        # Steam avatars: steam_web pins new sign-ins to the first host. The other two
        # stay because a session cookie keeps the avatar URL it was issued with for
        # 30 days, and earlier sign-ins stored whichever CDN host Steam returned.
        "https://avatars.steamstatic.com",
        "https://avatars.akamai.steamstatic.com",
        "https://avatars.fastly.steamstatic.com",
    ),
    # Umami's beacon is a fetch() to <script host>/api/e.
    "connect-src": ("'self'", ANALYTICS_ORIGIN),
    "object-src": ("'none'",),
    "base-uri": ("'none'",),
    "form-action": ("'self'",),
    "frame-ancestors": ("'none'",),
}

CONTENT_SECURITY_POLICY = "; ".join(
    f"{name} {' '.join(sources)}" for name, sources in CSP_DIRECTIVES.items()
)

HSTS = "max-age=31536000; includeSubDomains"

SECURITY_HEADERS: dict[str, str] = {
    "content-security-policy": CONTENT_SECURITY_POLICY,
    "x-content-type-options": "nosniff",
    # frame-ancestors covers this in current browsers; older ones only read this.
    "x-frame-options": "DENY",
    "referrer-policy": "strict-origin-when-cross-origin",
}


class SecurityHeaders:
    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        https = scope.get("scheme", "http") == "https"

        async def send_with_headers(message: Message) -> None:
            if message["type"] == "http.response.start":
                # headers is optional on this message in the ASGI spec.
                message.setdefault("headers", [])
                headers = MutableHeaders(scope=message)
                for name, value in SECURITY_HEADERS.items():
                    headers.setdefault(name, value)
                if https:
                    headers.setdefault("strict-transport-security", HSTS)
            await send(message)

        await self.app(scope, receive, send_with_headers)
