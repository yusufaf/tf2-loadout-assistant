"""FastAPI app for the loadout assistant.

Built from a CatalogService + PricingService so it can be exercised with fixtures in
tests and booted from the on-disk cache in production (see ``main``).
"""

from __future__ import annotations

import json
import logging
import secrets
from collections.abc import Sequence
from dataclasses import replace
from pathlib import Path
from typing import Annotated
from urllib.parse import quote_from_bytes, urlsplit

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse, Response, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import AfterValidator, BaseModel, Field, ValidationError, field_validator
from pydantic_ai.messages import (
    ModelMessage,
    ModelMessagesTypeAdapter,
    ModelRequest,
    RetryPromptPart,
    ToolReturnPart,
    UserPromptPart,
)
from pydantic_core import to_jsonable_python
from starlette.datastructures import Headers
from starlette.middleware.sessions import SessionMiddleware
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from tf2_loadout.agent import (
    LoadoutAgentService,
    LoadoutDeps,
    build_chat_service,
    turn_error,
)
from tf2_loadout.auth import SteamAuthError, SteamAuthService
from tf2_loadout.catalog import ALL_CLASSES, CatalogService, load_defindex_names
from tf2_loadout.config import (
    PROVIDERS,
    AuthSettings,
    LLMSettings,
    LoadoutsSettings,
    UserLLM,
    load_env,
    provider_spec,
)
from tf2_loadout.conflicts import MAX_EQUIPPED
from tf2_loadout.inventory import InventoryService
from tf2_loadout.loadouts import (
    DynamoLoadoutStore,
    LoadoutStore,
    LoadoutStoreError,
    SavedLoadout,
)
from tf2_loadout.lore import LoreService
from tf2_loadout.models import Cosmetic
from tf2_loadout.pricing import PricingService
from tf2_loadout.steam_web import SteamWebClient

logger = logging.getLogger(__name__)

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"
FRONTEND_DIST = Path(__file__).resolve().parents[2] / "frontend" / "dist"

# The player's own LLM credential rides each chat request in headers rather than the
# body or the session: the body is the transcript (which the client stores and sends
# back), and the session cookie is signed but not encrypted.
LLM_PROVIDER_HEADER = "X-LLM-Provider"
LLM_KEY_HEADER = "X-LLM-API-Key"

# The Fly app's own hostname serves the same site as the custom domain; sending it to
# PUBLIC_BASE_URL keeps search engines and shared links on one origin.
REDIRECTED_HOSTS = frozenset({"tf2-loadout-af.fly.dev"})
HEALTHZ_PATHS = frozenset({"/healthz", "/healthz/"})

# RFC 3986 pchar beyond the unreserved set (which quote never escapes), plus "%" so an
# escape the client already sent passes through instead of being encoded twice.
_PATH_SAFE = "/%:@!$&'()*+,;="
_QUERY_SAFE = _PATH_SAFE + "?"


class CanonicalHostRedirect:
    """Redirect a request for a REDIRECTED_HOSTS name to the same path and query on
    ``canonical_origin``.

    301 for GET/HEAD; 308 for everything else, since clients may turn a 301'd POST
    into a body-less GET. Only an exact Host match redirects, so Fly's health checks
    and internal traffic (addressed by the machine's private IP or a .internal name)
    pass straight through. /healthz is exempt even on a matching host, in case a check
    is ever configured with the public name. Plain ASGI rather than
    ``@app.middleware("http")`` so the /chat/stream SSE body isn't routed through
    BaseHTTPMiddleware.
    """

    def __init__(self, app: ASGIApp, canonical_origin: str) -> None:
        self.app = app
        self.canonical_origin = canonical_origin

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] == "http" and scope["path"] not in HEALTHZ_PATHS:
            host = Headers(scope=scope).get("host", "").split(":")[0].lower()
            if host in REDIRECTED_HOSTS:
                # raw_path, not path: the decoded form would turn an escaped %3F
                # back into a query separator.
                raw_path = scope.get("raw_path") or scope["path"].encode()
                path = quote_from_bytes(raw_path, _PATH_SAFE)
                query = quote_from_bytes(scope["query_string"], _QUERY_SAFE)
                url = self.canonical_origin + path + (f"?{query}" if query else "")
                status = 301 if scope["method"] in ("GET", "HEAD") else 308
                # Not RedirectResponse: it would quote() the already-quoted URL again.
                response = Response(status_code=status, headers={"location": url})
                await response(scope, receive, send)
                return
        await self.app(scope, receive, send)


# Pydantic's max_length checks run only after FastAPI has buffered and parsed the whole
# body, so this bounds the read itself. The largest honest request -- a chat turn at
# every cap below (MAX_HISTORY_CHARS of history plus a full message) -- is ~410 KB.
MAX_REQUEST_BODY_BYTES = 1024 * 1024
BODY_TOO_LARGE = "request body too large"
# On a kept-alive connection uvicorn reads and discards whatever body is left after the
# response, so a 413 alone bounds memory but not how much of the upload this server
# reads. Copied per use: HTTPException keeps the dict it is given.
CLOSE_CONNECTION = {"connection": "close"}


class RequestBodyLimit:
    """413 any request whose body passes ``max_bytes``, before a handler parses it.

    A declared ``Content-Length`` over the limit is refused without reading the body or
    calling the app. Otherwise (chunked, or a lying/malformed header) ``receive`` is
    wrapped to count bytes and raise once the total passes the limit, so reading stops
    at that chunk. It raises HTTPException rather than answering itself because the
    read happens inside FastAPI's body parsing, which turns any other exception into a
    400 but re-raises HTTPException for the exception handler. Plain ASGI for the same
    reason as CanonicalHostRedirect.
    """

    def __init__(self, app: ASGIApp, max_bytes: int = MAX_REQUEST_BODY_BYTES) -> None:
        self.app = app
        self.max_bytes = max_bytes

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        try:
            declared = int(Headers(scope=scope).get("content-length"))
        except (TypeError, ValueError):  # absent (None) or malformed: count instead
            declared = None
        if declared is not None and declared > self.max_bytes:
            response = JSONResponse(
                {"detail": BODY_TOO_LARGE}, status_code=413, headers=dict(CLOSE_CONNECTION)
            )
            await response(scope, receive, send)
            return

        received = 0

        async def limited_receive() -> Message:
            nonlocal received
            message = await receive()
            if message["type"] == "http.request":
                received += len(message.get("body", b""))
                if received > self.max_bytes:
                    raise HTTPException(
                        status_code=413, detail=BODY_TOO_LARGE, headers=dict(CLOSE_CONNECTION)
                    )
            return message

        await self.app(scope, limited_receive, send)


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
    defindexes: list[int] = Field(max_length=MAX_EQUIPPED)


# A player's saved-loadout list is unbounded, so the frontend splits a hydration lookup
# into chunks of this size (frontend/src/api.ts mirrors the number).
MAX_COSMETIC_LOOKUP = 500


class CosmeticLookupRequest(BaseModel):
    defindexes: list[int] = Field(max_length=MAX_COSMETIC_LOOKUP)


# Chat is stateless: the client holds the transcript and sends it back each turn, which
# keeps the server a pure function of its injected services -- like the rest of the app,
# whose state already lives in localStorage. The cap stops a client blowing the context
# window (or our token budget) with an unbounded transcript. The same caps bound what the
# server hands back (``fit_history``), so a well-behaved client never trips them.
MAX_HISTORY_MESSAGES = 40

# The count cap alone lets one entry be arbitrarily large. 400k characters is a generous
# multiple of a realistic transcript (an 8-request turn with full searches is ~35-60k).
MAX_HISTORY_CHARS = 400_000

# A described look plus the "[Current class: ...]" prefix the client adds -- no honest
# message needs more, and every character is billed to the player's own key.
MAX_MESSAGE_CHARS = 2000

# Far past any honest loadout name; a few huge ones would push a player's rows past
# DynamoDB's 1 MB query page.
MAX_LOADOUT_NAME_CHARS = 100


def history_chars(entries: Sequence[dict]) -> int:
    """Serialized size of a transcript, the one measure both caps and trimming use."""
    return sum(len(json.dumps(e)) for e in entries)


def _starts_turn(message: ModelMessage) -> bool:
    return isinstance(message, ModelRequest) and any(
        isinstance(p, UserPromptPart) for p in message.parts
    )


def fit_history(
    messages: Sequence[ModelMessage],
    max_messages: int = MAX_HISTORY_MESSAGES,
    max_chars: int = MAX_HISTORY_CHARS,
) -> list[ModelMessage]:
    """The newest whole turns of ``messages`` that fit both history caps.

    The client resends whatever it is handed, so the server trims to what it would accept
    next turn -- otherwise a few tool-heavy turns (~17 messages each) push a normal
    conversation past the cap and the fourth message 422s. Cutting only at a user prompt
    keeps every tool call paired with its return. Returns ``[]`` when even the newest
    turn is over a cap, so the next turn starts fresh instead of failing.

    Size is measured per entry the same way ``history_chars`` does, so a trimmed history
    always passes ``ChatRequest``'s own check.
    """
    start, count, chars = len(messages), 0, 0
    for i in range(len(messages) - 1, -1, -1):
        count += 1
        chars += len(json.dumps(to_jsonable_python(messages[i])))
        if count > max_messages or chars > max_chars:
            break
        if _starts_turn(messages[i]):
            start = i
    kept = list(messages[start:])
    if kept:
        # pydantic-ai can merge the previous turn's final tool return into the request
        # that starts the next turn. That turn was just dropped, so its return would be
        # an orphan with no call.
        first = kept[0]
        kept[0] = replace(
            first,
            parts=[
                p for p in first.parts if not isinstance(p, ToolReturnPart | RetryPromptPart)
            ],
        )
    return kept


class ChatRequest(BaseModel):
    message: str = Field(max_length=MAX_MESSAGE_CHARS)
    history: list[dict] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)

    @field_validator("history")
    @classmethod
    def _history_fits(cls, history: list[dict]) -> list[dict]:
        if history_chars(history) > MAX_HISTORY_CHARS:
            raise ValueError("chat history is too large")
        return history

    # Defindexes currently on the player's bench, so the agent can carry them forward and
    # conflict-check against them instead of re-guessing from names in the prompt.
    equipped: list[int] = Field(default_factory=list, max_length=MAX_EQUIPPED)


class ChatResponse(BaseModel):
    message: str
    suggested_defindexes: list[int]
    conflicts: list[ConflictOut]
    history: list[dict]


class LoadoutOut(BaseModel):
    id: str
    name: str
    cls: str
    defindexes: list[int]
    created_at: int
    updated_at: int


def _known_class(value: str) -> str:
    if value not in ALL_CLASSES:
        raise ValueError("unknown class")
    return value


# Request-side only: LoadoutOut stays unconstrained so rows saved before these caps
# existed still read.
LoadoutName = Annotated[str, Field(max_length=MAX_LOADOUT_NAME_CHARS)]
ClassName = Annotated[str, AfterValidator(_known_class)]


class LoadoutCreate(BaseModel):
    name: LoadoutName
    cls: ClassName
    defindexes: list[int] = Field(default_factory=list, max_length=MAX_EQUIPPED)


class LoadoutPatch(BaseModel):
    name: LoadoutName | None = None
    cls: ClassName | None = None
    defindexes: list[int] | None = Field(default=None, max_length=MAX_EQUIPPED)


def create_app(
    catalog: CatalogService,
    pricing: PricingService,
    lore: LoreService | None = None,
    chat: LoadoutAgentService | None = None,
    auth: SteamAuthService | None = None,
    session_secret: str | None = None,
    https_only: bool = True,
    inventory: InventoryService | None = None,
    loadouts: LoadoutStore | None = None,
    public_base_url: str | None = None,
) -> FastAPI:
    app = FastAPI(title="TF2 Loadout Assistant")
    # Added first so it's innermost: its 413 passes through CORS and the session like
    # any other error, and a wrong-host POST still gets the redirect first.
    app.add_middleware(RequestBodyLimit)
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
    # Added last so it's outermost: a redirect never touches the session. Skipped when
    # PUBLIC_BASE_URL is itself a redirected host, which would redirect to itself.
    canonical = urlsplit(public_base_url) if public_base_url else None
    if canonical and canonical.hostname and canonical.hostname not in REDIRECTED_HOSTS:
        app.add_middleware(
            CanonicalHostRedirect,
            canonical_origin=f"{canonical.scheme}://{canonical.netloc}",
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

        A suggestion replaces the bench, so more than ``MAX_EQUIPPED`` is never
        meaningful; it is truncated rather than rejected, since it is model output and
        not client input.
        """
        seen: set[int] = set()
        suggested: list[int] = []
        for di in defindexes:
            if di in seen or not catalog.get(di):
                continue
            seen.add(di)
            suggested.append(di)
            if len(suggested) == MAX_EQUIPPED:
                break
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

    def _user_llm(request: Request) -> UserLLM:
        """The player's own provider + key, from the request headers.

        The server holds no LLM credential, so a turn without both is a 401 -- the
        same status a rejected key gets, so the UI has exactly one signal meaning
        "fix your key". Nothing here logs or echoes the key.
        """
        provider = (request.headers.get(LLM_PROVIDER_HEADER) or "").strip()
        api_key = (request.headers.get(LLM_KEY_HEADER) or "").strip()
        if not provider or not api_key:
            raise HTTPException(status_code=401, detail="llm key required")
        if provider_spec(provider) is None:
            raise HTTPException(status_code=401, detail="unknown llm provider")
        return UserLLM(provider=provider, api_key=api_key)

    @app.get("/chat/providers")
    def chat_providers() -> dict:
        """The allowlist the key form's dropdown is built from. Static, so it is
        served even when the chat service itself failed to construct."""
        return {
            "providers": [
                {"id": spec.id, "label": spec.label, "model": spec.model}
                for spec in PROVIDERS
            ]
        }

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "cosmetics": len(catalog),
            "priced": len(pricing),
            "chat": chat is not None,
            "auth": auth is not None,
            "inventory": inventory is not None,
            "loadouts": loadouts is not None,
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

    @app.post("/cosmetics/lookup")
    def lookup_cosmetics(req: CosmeticLookupRequest) -> dict:
        """Resolve many defindexes in one request.

        Saved loadouts on a Steam account store only defindexes, so hydrating them
        would otherwise be one ``GET /cosmetics/{defindex}`` per item. Unknown
        defindexes come back in ``missing`` instead of failing the whole call.
        """
        items: list[CosmeticOut] = []
        missing: list[int] = []
        for defindex in dict.fromkeys(req.defindexes):
            cosmetic = catalog.get(defindex)
            if cosmetic is None:
                missing.append(defindex)
            else:
                items.append(to_out(cosmetic))
        return {"items": items, "missing": missing}

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
        llm = _user_llm(request)
        owned = await _owned_for(request)
        try:
            result = await chat.reply(req.message, history, req.equipped, owned, llm)
        except Exception as exc:
            error = turn_error(exc)
            status = 401 if error.get("code") == "bad_key" else 502
            raise HTTPException(status_code=status, detail=error["detail"])
        suggested, conflicts = _validate_suggestions(
            result.output.suggested_defindexes
        )
        return ChatResponse(
            message=result.output.message,
            suggested_defindexes=suggested,
            conflicts=conflicts,
            history=to_jsonable_python(fit_history(result.all_messages())),
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
        # Resolved before the stream opens so a missing key is a real 401, not an
        # in-band error line on a 200.
        llm = _user_llm(request)
        owned = await _owned_for(request)

        async def lines():
            async for event in chat.stream_reply(
                req.message, history, req.equipped, owned, llm
            ):
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
                        "history": to_jsonable_python(fit_history(result.all_messages())),
                    }
                yield json.dumps(event) + "\n"

        return StreamingResponse(lines(), media_type="application/x-ndjson")

    @app.post("/loadout/conflicts")
    def loadout_conflicts(req: ConflictRequest) -> dict:
        # dict.fromkeys de-duplicates in order: a repeated id would otherwise "conflict"
        # with itself.
        cosmetics = [c for di in dict.fromkeys(req.defindexes) if (c := catalog.get(di))]
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

    def _loadout_out(saved: SavedLoadout) -> LoadoutOut:
        return LoadoutOut(
            id=saved.id,
            name=saved.name,
            cls=saved.cls,
            defindexes=list(saved.defindexes),
            created_at=saved.created_at,
            updated_at=saved.updated_at,
        )

    def _require_steam_id(request: Request) -> str:
        steam_id = request.session.get("steam_id")
        if not steam_id:
            raise HTTPException(status_code=401, detail="sign in required")
        return steam_id

    @app.exception_handler(LoadoutStoreError)
    async def _loadout_store_error(request: Request, exc: LoadoutStoreError) -> JSONResponse:
        logger.error("loadouts store failed", exc_info=exc)
        return JSONResponse(status_code=502, content={"detail": "loadouts storage error"})

    @app.get("/me/loadouts")
    async def list_loadouts(request: Request) -> dict:
        if auth is None or loadouts is None:
            raise HTTPException(status_code=503, detail="loadouts service unavailable")
        steam_id = _require_steam_id(request)
        items = await loadouts.list(steam_id)
        return {"loadouts": [_loadout_out(i) for i in items]}

    @app.post("/me/loadouts", status_code=201)
    async def create_loadout(req: LoadoutCreate, request: Request) -> LoadoutOut:
        if auth is None or loadouts is None:
            raise HTTPException(status_code=503, detail="loadouts service unavailable")
        steam_id = _require_steam_id(request)
        created = await loadouts.create(steam_id, req.name, req.cls, req.defindexes)
        return _loadout_out(created)

    @app.patch("/me/loadouts/{loadout_id}")
    async def patch_loadout(
        loadout_id: str, req: LoadoutPatch, request: Request
    ) -> LoadoutOut:
        if auth is None or loadouts is None:
            raise HTTPException(status_code=503, detail="loadouts service unavailable")
        steam_id = _require_steam_id(request)
        updated = await loadouts.update(
            steam_id, loadout_id, name=req.name, cls=req.cls, defindexes=req.defindexes
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="loadout not found")
        return _loadout_out(updated)

    @app.delete("/me/loadouts/{loadout_id}", status_code=204)
    async def delete_loadout(loadout_id: str, request: Request) -> Response:
        if auth is None or loadouts is None:
            raise HTTPException(status_code=503, detail="loadouts service unavailable")
        steam_id = _require_steam_id(request)
        deleted = await loadouts.delete(steam_id, loadout_id)
        if not deleted:
            raise HTTPException(status_code=404, detail="loadout not found")
        return Response(status_code=204)

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
    if chat is not None:
        print("chat enabled: players bring their own provider key per request")

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

    loadouts_settings = LoadoutsSettings.from_env()
    loadouts = None
    if loadouts_settings.enabled:
        loadouts = DynamoLoadoutStore(
            loadouts_settings.table_name, region=loadouts_settings.region
        )
        print(f"loadouts enabled: {loadouts_settings.table_name}")
    else:
        print("loadouts disabled: no DYNAMODB_TABLE_NAME found (see .env.example)")

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
        loadouts=loadouts,
        public_base_url=auth_settings.public_base_url,
    )
    # 0.0.0.0, not 127.0.0.1: inside a container, Fly's proxy connects from
    # outside the container's network namespace and can't reach a loopback bind.
    # proxy_headers: Fly terminates TLS in front of us, so trust its X-Forwarded-*
    # or every request looks like plain http even in production.
    uvicorn.run(app, host="0.0.0.0", port=8000, proxy_headers=True, forwarded_allow_ips="*")


if __name__ == "__main__":
    main()
