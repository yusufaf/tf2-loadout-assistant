"""FastAPI app for the loadout assistant.

Built from a CatalogService + PricingService so it can be exercised with fixtures in
tests and booted from the on-disk cache in production (see ``main``).
"""

from __future__ import annotations

import json
import secrets
from pathlib import Path

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_core import to_jsonable_python
from starlette.middleware.sessions import SessionMiddleware

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_chat_service
from tf2_loadout.auth import SteamAuthError, SteamAuthService
from tf2_loadout.catalog import CatalogService, load_defindex_names
from tf2_loadout.config import AuthSettings, LLMSettings, load_env
from tf2_loadout.inventory import InventoryService
from tf2_loadout.lore import LoreService
from tf2_loadout.models import Cosmetic
from tf2_loadout.pricing import PricingService
from tf2_loadout.steam_web import SteamWebClient

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"


class PriceOut(BaseModel):
    currency: str
    value: float
    value_high: float | None = None
    # Normalized to refined metal so the frontend can sort/filter by budget without
    # re-implementing the keys exchange rate; None when the currency (hat, usd) has no
    # ref equivalent.
    ref_value: float | None = None


class CosmeticOut(BaseModel):
    defindex: int
    name: str
    equip_regions: list[str]
    used_by_classes: list[str]
    item_slot: str | None
    image_url: str | None
    price: PriceOut | None
    paintable: bool
    holiday_restriction: str | None
    styles: list[str]


class ConflictOut(BaseModel):
    a: int
    b: int
    regions: list[str]


class ConflictRequest(BaseModel):
    defindexes: list[int]


# Chat is stateless: the client holds the transcript and sends it back each turn, which
# keeps the server a pure function of its injected services -- like the rest of the app,
# whose state already lives in localStorage. The cap stops a client blowing the context
# window (or our token budget) with an unbounded transcript.
MAX_HISTORY_MESSAGES = 40

# The bench holds a handful of cosmetics at most; this just guards against garbage input,
# not a real usage ceiling.
MAX_EQUIPPED = 16


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)
    # Defindexes currently on the player's bench, so the agent can carry them forward and
    # conflict-check against them instead of re-guessing from names in the prompt.
    equipped: list[int] = Field(default_factory=list, max_length=MAX_EQUIPPED)


class ChatResponse(BaseModel):
    message: str
    suggested_defindexes: list[int]
    conflicts: list[ConflictOut]
    history: list[dict]


def create_app(
    catalog: CatalogService,
    pricing: PricingService,
    lore: LoreService | None = None,
    chat: LoadoutAgentService | None = None,
    auth: SteamAuthService | None = None,
    session_secret: str | None = None,
    https_only: bool = True,
    inventory: InventoryService | None = None,
) -> FastAPI:
    app = FastAPI(title="TF2 Loadout Assistant")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
    )
    if auth is not None and session_secret:
        app.add_middleware(
            SessionMiddleware,
            secret_key=session_secret,
            session_cookie="tf2_session",
            # The return from Steam is a top-level cross-site GET; "strict" would
            # drop the cookie carrying the CSRF state before the handler ever sees it.
            same_site="lax",
            https_only=https_only,
            max_age=30 * 24 * 3600,
        )

    def to_out(cosmetic: Cosmetic) -> CosmeticOut:
        price = pricing.get(cosmetic.defindex)
        return CosmeticOut(
            defindex=cosmetic.defindex,
            name=cosmetic.name,
            equip_regions=sorted(cosmetic.equip_regions),
            used_by_classes=list(cosmetic.used_by_classes),
            item_slot=cosmetic.item_slot,
            image_url=cosmetic.image_url,
            price=(
                PriceOut(**vars(price), ref_value=pricing.ref_value(price))
                if price
                else None
            ),
            paintable=cosmetic.paintable,
            holiday_restriction=cosmetic.holiday_restriction,
            styles=list(cosmetic.styles),
        )

    def _validate_suggestions(
        defindexes: list[int],
    ) -> tuple[list[int], list[ConflictOut]]:
        """Turn a raw model suggestion into what the client can trust.

        Weaker models name items they never looked up, so re-resolve against the catalog
        first -- a hallucinated defindex never leaves the API. Then run the survivors
        through the same conflict engine the tray uses: the agent is instructed to
        self-check with check_conflicts, but a lazy model can skip that, so the server
        checks again rather than trusting the prompt alone.
        """
        seen: set[int] = set()
        suggested: list[int] = []
        for di in defindexes:
            if di in seen or not catalog.get(di):
                continue
            seen.add(di)
            suggested.append(di)
        cosmetics = [c for di in suggested if (c := catalog.get(di))]
        conflicts = [
            ConflictOut(a=c.a.defindex, b=c.b.defindex, regions=sorted(c.regions))
            for c in catalog.conflicts(cosmetics)
        ]
        return suggested, conflicts

    async def _owned_for(request: Request) -> frozenset[int] | None:
        """The signed-in player's real backpack, for the agent's owned_only tool.

        None (not an empty set) whenever it isn't available -- signed out, no
        inventory service configured, or the backpack fetch itself didn't come back
        "ok" (private/not_found/error) -- so the agent says it can't see the
        backpack instead of quietly treating "unavailable" as "owns nothing".
        Chat is stateless in the transcript sense (CLAUDE.md), but inventory rides
        the session cookie same as identity, not the client-sent history.
        """
        if auth is None or inventory is None:
            return None
        steam_id = request.session.get("steam_id")
        if not steam_id:
            return None
        result = await inventory.fetch(steam_id)
        return result.defindexes if result.status == "ok" else None

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "cosmetics": len(catalog),
            "priced": len(pricing),
            "chat": chat is not None,
            "auth": auth is not None,
            "inventory": inventory is not None,
        }

    @app.get("/cosmetics")
    def list_cosmetics(
        used_by: str | None = None, q: str | None = None, limit: int = 100
    ) -> dict:
        """List cosmetics.

        ``limit=0`` means no limit — the browser filters client-side and needs the whole
        class list, not a truncated page of it.
        """
        items = catalog.for_class(used_by) if used_by else catalog.all()
        if q:
            needle = q.lower()
            items = [c for c in items if needle in c.name.lower()]
        if limit > 0:
            items = items[:limit]
        return {"items": [to_out(c) for c in items]}

    @app.get("/equip-conflicts")
    def equip_conflicts() -> dict:
        """The cross-region conflict matrix.

        Static and small, so the client fetches it once and evaluates clashes locally
        rather than round-tripping on every filter toggle.
        """
        return {
            "matrix": {
                region: sorted(others)
                for region, others in catalog.conflict_matrix.items()
            }
        }

    @app.get("/cosmetics/{defindex}")
    def get_cosmetic(defindex: int) -> CosmeticOut:
        cosmetic = catalog.get(defindex)
        if cosmetic is None:
            raise HTTPException(status_code=404, detail="cosmetic not found")
        return to_out(cosmetic)

    @app.get("/lore/{defindex}")
    async def get_lore(defindex: int) -> dict:
        cosmetic = catalog.get(defindex)
        if cosmetic is None:
            raise HTTPException(status_code=404, detail="cosmetic not found")
        if lore is None:
            raise HTTPException(status_code=503, detail="lore service unavailable")
        item_lore = await lore.get_lore(cosmetic.name)
        if item_lore is None:
            raise HTTPException(status_code=404, detail="no lore found")
        return {
            "defindex": defindex,
            "title": item_lore.title,
            "summary": item_lore.summary,
        }

    @app.post("/chat")
    async def chat_turn(req: ChatRequest, request: Request) -> ChatResponse:
        if chat is None:
            raise HTTPException(status_code=503, detail="chat service unavailable")
        try:
            history = ModelMessagesTypeAdapter.validate_python(req.history)
        except ValidationError:
            raise HTTPException(status_code=422, detail="malformed chat history")
        owned = await _owned_for(request)
        try:
            result = await chat.reply(req.message, history, req.equipped, owned)
        except UsageLimitExceeded:
            raise HTTPException(status_code=502, detail="the agent gave up mid-thought")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"model error: {exc}")
        suggested, conflicts = _validate_suggestions(
            result.output.suggested_defindexes
        )
        return ChatResponse(
            message=result.output.message,
            suggested_defindexes=suggested,
            conflicts=conflicts,
            history=to_jsonable_python(result.all_messages()),
        )

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest, request: Request) -> StreamingResponse:
        """Same turn as /chat, as newline-delimited JSON with tool progress.

        NDJSON rather than SSE because EventSource cannot POST a body, and the
        transcript is far too big for a query string.
        """
        if chat is None:
            raise HTTPException(status_code=503, detail="chat service unavailable")
        try:
            history = ModelMessagesTypeAdapter.validate_python(req.history)
        except ValidationError:
            raise HTTPException(status_code=422, detail="malformed chat history")
        owned = await _owned_for(request)

        async def lines():
            async for event in chat.stream_reply(req.message, history, req.equipped, owned):
                if event["kind"] == "final":
                    result = event["result"]
                    suggested, conflicts = _validate_suggestions(
                        result.output.suggested_defindexes
                    )
                    event = {
                        "kind": "final",
                        "message": result.output.message,
                        "suggested_defindexes": suggested,
                        "conflicts": [c.model_dump() for c in conflicts],
                        "history": to_jsonable_python(result.all_messages()),
                    }
                yield json.dumps(event) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.post("/loadout/conflicts")
    def loadout_conflicts(req: ConflictRequest) -> dict:
        cosmetics = [c for di in req.defindexes if (c := catalog.get(di))]
        conflicts = catalog.conflicts(cosmetics)
        return {
            "conflicts": [
                ConflictOut(a=c.a.defindex, b=c.b.defindex, regions=sorted(c.regions))
                for c in conflicts
            ]
        }

    @app.get("/auth/steam/login")
    def steam_login(request: Request) -> RedirectResponse:
        if auth is None:
            raise HTTPException(status_code=503, detail="auth service unavailable")
        state = secrets.token_urlsafe(16)
        request.session["oauth_state"] = state
        return RedirectResponse(auth.login_url(state))

    @app.get("/auth/steam/return")
    async def steam_return(request: Request) -> RedirectResponse:
        if auth is None:
            raise HTTPException(status_code=503, detail="auth service unavailable")
        query = dict(request.query_params)
        expected_state = request.session.pop("oauth_state", None)
        if not expected_state or query.get("state") != expected_state:
            return RedirectResponse("/?auth=failed")
        openid_params = {k: v for k, v in query.items() if k.startswith("openid.")}
        try:
            user = await auth.complete_login(openid_params)
        except SteamAuthError:
            return RedirectResponse("/?auth=failed")
        request.session["steam_id"] = user.steam_id
        request.session["persona"] = user.persona
        request.session["avatar"] = user.avatar
        return RedirectResponse("/")

    @app.get("/auth/me")
    def auth_me(request: Request) -> dict:
        if auth is None:
            raise HTTPException(status_code=503, detail="auth service unavailable")
        steam_id = request.session.get("steam_id")
        if not steam_id:
            return {"signed_in": False}
        return {
            "signed_in": True,
            "steam_id": steam_id,
            "persona": request.session.get("persona"),
            "avatar": request.session.get("avatar"),
        }

    @app.post("/auth/logout")
    def auth_logout(request: Request) -> Response:
        if auth is None:
            raise HTTPException(status_code=503, detail="auth service unavailable")
        request.session.clear()
        return Response(status_code=204)

    @app.get("/me/inventory")
    async def me_inventory(request: Request, refresh: bool = False) -> dict:
        if auth is None or inventory is None:
            raise HTTPException(status_code=503, detail="inventory service unavailable")
        steam_id = request.session.get("steam_id")
        if not steam_id:
            raise HTTPException(status_code=401, detail="sign in required")
        result = await inventory.fetch(steam_id, refresh=refresh)
        return {
            "status": result.status,
            "defindexes": sorted(result.defindexes),
            "fetched_at": result.fetched_at,
        }

    # Serves the built frontend at the same origin as the API, so tf2.yusufaf.dev
    # needs no CORS config and no separate static host. Mounted last so it never
    # shadows the API routes above — Starlette matches explicit paths before a
    # Mount. html=True serves index.html for "/"; there's no client-side router
    # to fall back for, so a plain static mount is enough.
    if FRONTEND_DIST.is_dir():
        app.mount("/", StaticFiles(directory=FRONTEND_DIST, html=True), name="frontend")

    return app


def main() -> None:
    """Boot the app from the on-disk cache and serve with uvicorn."""
    import uvicorn

    from tf2_wiki_mcp.client import WikiClient

    load_env()
    catalog = CatalogService.from_cache(CACHE_DIR)
    pricing = PricingService.from_cache(CACHE_DIR)
    lore = LoreService(WikiClient(), cache_dir=CACHE_DIR)

    settings = LLMSettings.from_env()
    chat = build_chat_service(
        settings, LoadoutDeps(catalog=catalog, pricing=pricing, lore=lore)
    )
    if chat is None and not settings.enabled:
        print("chat disabled: no LLM key found (see .env.example)")
    elif chat is not None:
        print(f"chat enabled: {settings.model}")

    auth_settings = AuthSettings.from_env()
    auth = None
    if auth_settings.enabled:
        auth = SteamAuthService(
            public_base_url=auth_settings.public_base_url,
            steam_api_key=auth_settings.steam_api_key,
        )
        print(f"auth enabled: {auth_settings.public_base_url}")
    else:
        print("auth disabled: no SESSION_SECRET/PUBLIC_BASE_URL found (see .env.example)")

    # Needs both a working session (to know whose backpack to fetch) and a Steam key
    # (to fetch it), so it rides on auth rather than being independently configurable.
    inventory = None
    if auth is not None and auth_settings.steam_api_key:
        inventory = InventoryService(
            SteamWebClient(auth_settings.steam_api_key),
            catalog,
            load_defindex_names(CACHE_DIR),
        )

    app = create_app(
        catalog,
        pricing,
        lore,
        chat,
        auth=auth,
        session_secret=auth_settings.session_secret,
        https_only=bool(
            auth_settings.public_base_url
            and auth_settings.public_base_url.startswith("https://")
        ),
        inventory=inventory,
    )
    # 0.0.0.0, not 127.0.0.1: inside a container, Fly's proxy connects from
    # outside the container's network namespace and can't reach a loopback bind.
    # proxy_headers: Fly terminates TLS in front of us, so trust its X-Forwarded-*
    # or every request looks like plain http even in production.
    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
