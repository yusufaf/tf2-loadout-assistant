"""FastAPI app for the loadout assistant.

Built from a CatalogService + PricingService so it can be exercised with fixtures in
tests and booted from the on-disk cache in production (see ``main``).
"""

from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from tf2_loadout.catalog import CatalogService
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


def create_app(
    catalog: CatalogService,
    pricing: PricingService,
    lore: LoreService | None = None,
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
        return {"status": "ok", "cosmetics": len(catalog), "priced": len(pricing)}

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

    catalog = CatalogService.from_cache(CACHE_DIR)
    pricing = PricingService.from_cache(CACHE_DIR)
    lore = LoreService(WikiClient(), cache_dir=CACHE_DIR)
    uvicorn.run(create_app(catalog, pricing, lore), host="127.0.0.1", port=8000)


if __name__ == "__main__":
    main()
