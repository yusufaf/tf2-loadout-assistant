"""FastAPI app for the loadout assistant.

Built from a CatalogService + PricingService so it can be exercised with fixtures in
tests and booted from the on-disk cache in production (see ``main``).
"""

from __future__ import annotations

import json
from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field, ValidationError
from pydantic_ai.exceptions import UsageLimitExceeded
from pydantic_ai.messages import ModelMessagesTypeAdapter
from pydantic_core import to_jsonable_python

from tf2_loadout.agent import LoadoutAgentService, LoadoutDeps, build_chat_service
from tf2_loadout.catalog import CatalogService
from tf2_loadout.config import LLMSettings, load_env
from tf2_loadout.lore import LoreService
from tf2_loadout.models import Cosmetic
from tf2_loadout.pricing import PricingService

CACHE_DIR = Path(__file__).resolve().parents[2] / ".cache"


class PriceOut(BaseModel):
    currency: str
    value: float
    value_high: float | None = None


class CosmeticOut(BaseModel):
    defindex: int
    name: str
    equip_regions: list[str]
    used_by_classes: list[str]
    item_slot: str | None
    image_url: str | None
    price: PriceOut | None


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


class ChatRequest(BaseModel):
    message: str
    history: list[dict] = Field(default_factory=list, max_length=MAX_HISTORY_MESSAGES)


class ChatResponse(BaseModel):
    message: str
    suggested_defindexes: list[int]
    history: list[dict]


def create_app(
    catalog: CatalogService,
    pricing: PricingService,
    lore: LoreService | None = None,
    chat: LoadoutAgentService | None = None,
) -> FastAPI:
    app = FastAPI(title="TF2 Loadout Assistant")
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["http://localhost:5173", "http://127.0.0.1:5173"],
        allow_methods=["*"],
        allow_headers=["*"],
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
            price=PriceOut(**vars(price)) if price else None,
        )

    @app.get("/healthz")
    def healthz() -> dict:
        return {
            "status": "ok",
            "cosmetics": len(catalog),
            "priced": len(pricing),
            "chat": chat is not None,
        }

    @app.get("/cosmetics")
    def list_cosmetics(used_by: str | None = None, q: str | None = None, limit: int = 100) -> dict:
        items = catalog.for_class(used_by) if used_by else catalog.all()
        if q:
            needle = q.lower()
            items = [c for c in items if needle in c.name.lower()]
        return {"items": [to_out(c) for c in items[:limit]]}

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
    async def chat_turn(req: ChatRequest) -> ChatResponse:
        if chat is None:
            raise HTTPException(status_code=503, detail="chat service unavailable")
        try:
            history = ModelMessagesTypeAdapter.validate_python(req.history)
        except ValidationError:
            raise HTTPException(status_code=422, detail="malformed chat history")
        try:
            result = await chat.reply(req.message, history)
        except UsageLimitExceeded:
            raise HTTPException(status_code=502, detail="the agent gave up mid-thought")
        except Exception as exc:
            raise HTTPException(status_code=502, detail=f"model error: {exc}")
        # Weaker models name items they never looked up. Re-resolve against the catalog
        # so a hallucinated defindex never leaves the API.
        suggested = [
            di for di in result.output.suggested_defindexes if catalog.get(di)
        ]
        return ChatResponse(
            message=result.output.message,
            suggested_defindexes=suggested,
            history=to_jsonable_python(result.all_messages()),
        )

    @app.post("/chat/stream")
    async def chat_stream(req: ChatRequest) -> StreamingResponse:
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

        async def lines():
            async for event in chat.stream_reply(req.message, history):
                if event["kind"] == "final":
                    result = event["result"]
                    event = {
                        "kind": "final",
                        "message": result.output.message,
                        "suggested_defindexes": [
                            di
                            for di in result.output.suggested_defindexes
                            if catalog.get(di)
                        ],
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

    uvicorn.run(create_app(catalog, pricing, lore, chat), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
