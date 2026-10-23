"""Security headers and the CSP, wired through create_app.

Covers that every kind of response carries the headers (routes, errors, redirects,
the chat stream), that HSTS needs https, and that the policy stays strict. The
index.html and catalog tests check the other side: that the page and the item icons
only reach hosts the policy allows.
"""

from __future__ import annotations

import json
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import urlsplit

import pytest
from fastapi.testclient import TestClient
from pydantic_ai.models.test import TestModel

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_agent
from tf2_loadout.api import CACHE_DIR, create_app
from tf2_loadout.auth import SignedInUser
from tf2_loadout.catalog import CatalogService, merge_catalog
from tf2_loadout.models import Cosmetic
from tf2_loadout.pricing import PricingService
from tf2_loadout.security_headers import (
    CSP_DIRECTIVES,
    HSTS,
    SECURITY_HEADERS,
    SecurityHeaders,
)

LLM_HEADERS = {"X-LLM-Provider": "anthropic", "X-LLM-API-Key": "sk-test"}
FEDORA = Cosmetic(1, "Spy Fedora", frozenset({"hat"}), ("Spy",), "misc", "img1")


class _StubAuth:
    def __init__(self, avatar: str | None = None) -> None:
        self.avatar = avatar

    async def complete_login(self, params):
        return SignedInUser(steam_id="1", persona="Spy", avatar=self.avatar, profile_url=None)

    def login_url(self, state: str) -> str:
        return f"https://steamcommunity.com/openid/login?state={state}"


class _ExplodingCatalog(CatalogService):
    def get(self, defindex: int) -> Cosmetic | None:
        raise RuntimeError("boom")


def _app(**kwargs):
    return create_app(CatalogService([FEDORA]), PricingService({}), **kwargs)


def _assert_security_headers(headers) -> None:
    for name, value in SECURITY_HEADERS.items():
        assert headers.get(name) == value, name


@pytest.mark.parametrize(
    ("method", "path", "body", "expected_status"),
    [
        ("GET", "/healthz", None, 200),
        ("GET", "/cosmetics/1", None, 200),
        ("GET", "/cosmetics/999", None, 404),
        ("POST", "/chat", {"message": "hi"}, 503),  # no chat service
        ("POST", "/chat", {}, 422),
    ],
)
def test_every_response_carries_the_security_headers(method, path, body, expected_status):
    r = TestClient(_app()).request(method, path, json=body)
    assert r.status_code == expected_status
    _assert_security_headers(r.headers)


def test_hsts_is_sent_only_over_https():
    app = _app()
    assert "strict-transport-security" not in TestClient(app).get("/healthz").headers
    r = TestClient(app, base_url="https://testserver").get("/healthz")
    assert r.headers["strict-transport-security"] == HSTS


def test_a_header_the_app_set_is_kept():
    async def raw_app(scope, receive, send):
        await send(
            {
                "type": "http.response.start",
                "status": 200,
                "headers": [(b"x-frame-options", b"SAMEORIGIN")],
            }
        )
        await send({"type": "http.response.body", "body": b"ok"})

    r = TestClient(SecurityHeaders(raw_app)).get("/")
    assert r.headers.get_list("x-frame-options") == ["SAMEORIGIN"]
    assert r.headers["content-security-policy"] == SECURITY_HEADERS["content-security-policy"]


def test_a_response_start_without_headers_still_gets_them():
    async def raw_app(scope, receive, send):
        await send({"type": "http.response.start", "status": 204})
        await send({"type": "http.response.body", "body": b""})

    r = TestClient(SecurityHeaders(raw_app)).get("/")
    assert r.status_code == 204
    _assert_security_headers(r.headers)


def test_the_steam_sign_in_redirect_carries_the_headers():
    client = TestClient(_app(auth=_StubAuth(), session_secret="s", https_only=False))
    r = client.get("/auth/steam/login", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"].startswith("https://steamcommunity.com/")
    _assert_security_headers(r.headers)


def test_a_session_avatar_is_served_from_an_allowed_host():
    """The cookie may hold whatever host Steam returned at sign-in (any session up to
    30 days old predates the pinning), so /auth/me pins it on the way out."""
    stored = "http://avatars.akamai.steamstatic.com/abc_full.jpg?t=1"
    client = TestClient(_app(auth=_StubAuth(stored), session_secret="s", https_only=False))
    login = client.get("/auth/steam/login", follow_redirects=False)
    state = login.headers["location"].split("state=", 1)[1]
    client.get(
        "/auth/steam/return",
        params={"state": state, "openid.mode": "id_res"},
        follow_redirects=False,
    )

    avatar = client.get("/auth/me").json()["avatar"]
    assert avatar == "https://avatars.steamstatic.com/abc_full.jpg?t=1"
    assert _origin(avatar) in CSP_DIRECTIVES["img-src"]


def test_the_canonical_host_redirect_carries_the_headers():
    app = _app(public_base_url="https://tf2.yusufaf.dev")
    client = TestClient(app, base_url="https://tf2-loadout-af.fly.dev")
    r = client.get("/", follow_redirects=False)
    assert r.status_code == 301
    _assert_security_headers(r.headers)
    assert r.headers["strict-transport-security"] == HSTS


def test_the_chat_stream_still_streams_under_the_headers():
    catalog = CatalogService([FEDORA])
    pricing = PricingService({})
    model = TestModel(
        custom_output_args={"message": "Try the fedora.", "suggested_defindexes": [1]}
    )
    chat = LoadoutAgentService(
        build_agent(None),
        LoadoutDeps(catalog=catalog, pricing=pricing, lore=None),
        model_factory=lambda llm: model,
    )
    client = TestClient(create_app(catalog, pricing, None, chat))

    r = client.post("/chat/stream", json={"message": "dress my Spy"}, headers=LLM_HEADERS)
    assert r.status_code == 200
    _assert_security_headers(r.headers)
    lines = [json.loads(line) for line in r.text.splitlines() if line.strip()]
    assert lines[-1]["kind"] == "final"


def test_the_policy_never_allows_inline_eval_or_wildcards():
    forbidden = {
        "'unsafe-inline'", "'unsafe-eval'", "'unsafe-hashes'", "*", "data:", "http:", "https:"
    }
    for name, sources in CSP_DIRECTIVES.items():
        assert not forbidden & set(sources), name
        for source in sources:
            assert source.startswith("'") or source.startswith("https://"), (name, source)
    assert CSP_DIRECTIVES["object-src"] == ("'none'",)
    assert CSP_DIRECTIVES["frame-ancestors"] == ("'none'",)
    assert CSP_DIRECTIVES["base-uri"] == ("'none'",)


def test_an_unhandled_500_is_the_one_exemption():
    """Starlette's ServerErrorMiddleware sits outside every user middleware, so its
    500 goes out without the headers. Its body is fixed plain text with nothing an
    attacker controls. If this starts failing, the exemption is gone: delete this test
    and the CLAUDE.md caveat."""
    app = create_app(_ExplodingCatalog([FEDORA]), PricingService({}))
    r = TestClient(app, raise_server_exceptions=False).get("/cosmetics/1")
    assert r.status_code == 500
    assert "content-security-policy" not in r.headers


def _origin(url: str) -> str:
    parts = urlsplit(url)
    return f"{parts.scheme}://{parts.netloc}"


@pytest.mark.skipif(
    not all((CACHE_DIR / f).exists() for f in ("schema_items.json", "equip.json")),
    reason="no local catalog cache (rebuild with --live)",
)
def test_cached_catalog_images_are_allowed_by_img_src():
    catalog = CatalogService.from_cache(CACHE_DIR)
    origins = {_origin(c.image_url) for c in catalog.all() if c.image_url}
    assert origins
    assert origins <= set(CSP_DIRECTIVES["img-src"])


def test_a_schema_icon_url_is_allowed_by_img_src():
    """CI has no cache, so this runs the real feed's scheme and host through the merge."""
    raw = {
        "defindex": 116,
        "item_class": "tf_wearable",
        "item_name": "The Modest Pile of Hat",
        "image_url": "http://media.steampowered.com/apps/440/icons/modest_hat.png",
        "used_by_classes": ["Scout"],
    }
    [cosmetic] = merge_catalog([raw], {116: frozenset({"hat"})})
    assert _origin(cosmetic.image_url) in CSP_DIRECTIVES["img-src"]


FRONTEND = Path(__file__).resolve().parents[1] / "frontend"


# Which directive governs a <link rel=preload as=...>; anything else falls to default-src.
_PRELOAD_DIRECTIVES = {
    "script": "script-src",
    "style": "style-src",
    "font": "font-src",
    "image": "img-src",
    "fetch": "connect-src",
}


class _PageScan(HTMLParser):
    """Collects (directive, url) for every resource the page asks the browser to load."""

    def __init__(self) -> None:
        super().__init__()
        self.loads: list[tuple[str, str]] = []
        self.inline_scripts = 0
        self.styled_tags: list[str] = []

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if "style" in a:
            self.styled_tags.append(tag)
        if tag == "script":
            if "src" in a:
                self.loads.append(("script-src", a["src"] or ""))
            else:
                self.inline_scripts += 1
        elif tag == "img":
            self.loads.append(("img-src", a.get("src") or ""))
        elif tag == "link":
            rel = set((a.get("rel") or "").lower().split())
            href = a.get("href") or ""
            if "stylesheet" in rel:
                self.loads.append(("style-src", href))
            elif {"icon", "apple-touch-icon"} & rel:
                self.loads.append(("img-src", href))
            elif "modulepreload" in rel:
                self.loads.append(("script-src", href))
            elif {"preload", "prefetch", "manifest"} & rel:
                kind = (a.get("as") or "").lower()
                self.loads.append((_PRELOAD_DIRECTIVES.get(kind, "default-src"), href))


def _source(url: str) -> str:
    """The CSP source a URL needs: 'self' for a same-origin URL, else its origin.
    A protocol-relative URL is cross-origin too (https on the deployed page)."""
    parts = urlsplit(url)
    if parts.netloc:
        return f"{parts.scheme or 'https'}://{parts.netloc}"
    # data:, blob: and friends are scheme sources, which the policy never lists.
    return f"{parts.scheme}:" if parts.scheme else "'self'"


@pytest.mark.parametrize("page", ["index.html", "dist/index.html"])
def test_index_html_loads_only_allowlisted_origins(page):
    path = FRONTEND / page
    if not path.exists():
        pytest.skip(f"{page} not built")
    scan = _PageScan()
    scan.feed(path.read_text(encoding="utf-8"))

    assert any(directive == "script-src" for directive, _ in scan.loads)
    for directive, url in scan.loads:
        assert url, f"empty URL for {directive}"
        assert _source(url) in CSP_DIRECTIVES[directive], (directive, url)
    assert scan.inline_scripts == 0
    assert scan.styled_tags == []


def test_the_page_scan_catches_what_the_policy_would_block():
    scan = _PageScan()
    scan.feed(
        '<script src="//cdn.example.com/x.js"></script>'
        '<link rel="Stylesheet" href="https://cdn.example.com/x.css">'
        '<link rel="preload" as="font" href="https://fonts.gstatic.com/x.woff2">'
        '<link rel="stylesheet">'
        '<link rel="preload" as="fetch" href="https://analytics.yusufaf.dev/api/e">'
    )
    assert scan.loads == [
        ("script-src", "//cdn.example.com/x.js"),
        ("style-src", "https://cdn.example.com/x.css"),
        ("font-src", "https://fonts.gstatic.com/x.woff2"),
        ("style-src", ""),
        ("connect-src", "https://analytics.yusufaf.dev/api/e"),
    ]
    assert _source("data:image/png;base64,AAAA") == "data:"
    assert _source("//cdn.example.com/x.js") == "https://cdn.example.com"
    assert _source("https://fonts.gstatic.com/x.woff2") in CSP_DIRECTIVES["font-src"]
