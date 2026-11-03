"""Kavrelle HTTP API (phase 0: health check and the mall directory)."""

from __future__ import annotations

from collections.abc import Iterator

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import BaseModel
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, ping
from kavrelle.models import Product, Shop


class VariantOut(BaseModel):
    label: str
    in_stock: bool


class ProductOut(BaseModel):
    slug: str
    name: str
    color: str
    price_cents: int
    currency: str
    variant_label: str
    variants: list[VariantOut]
    image_url: str | None
    image_credit: str | None


class ShopSummary(BaseModel):
    slug: str
    name: str
    category: str
    floor: int
    position: int
    product_count: int


class ShopDetail(BaseModel):
    slug: str
    name: str
    category: str
    floor: int
    description: str
    products: list[ProductOut]


def _product_out(p: Product) -> ProductOut:
    return ProductOut(
        slug=p.slug, name=p.name, color=p.color, price_cents=p.price_cents, currency=p.currency,
        variant_label=p.variant_label, image_url=p.image_url, image_credit=p.image_credit,
        variants=[VariantOut(label=v.label, in_stock=v.stock > 0) for v in p.variants],
    )  # fmt: skip


def create_app(
    settings: Settings | None = None, engine: Engine | None = None
) -> FastAPI:
    settings = settings or Settings.load()
    engine = engine or make_engine(settings.database_url)
    factory: sessionmaker[Session] = make_session_factory(engine)
    app = FastAPI(
        title=settings.brand, docs_url="/api/docs", openapi_url="/api/openapi.json"
    )

    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    @app.get("/api/health")
    def health() -> JSONResponse:
        ok = ping(engine)
        body = {"status": "ok" if ok else "degraded", "database": "ok" if ok else "down",
                "brand": settings.brand}  # fmt: skip
        return JSONResponse(body, status_code=200 if ok else 503)

    @app.get("/api/shops", response_model=list[ShopSummary])
    def list_shops(session: Session = Depends(get_session)) -> list[ShopSummary]:  # noqa: B008
        counts = (
            select(Product.shop_id, func.count(Product.id).label("n"))
            .where(Product.active.is_(True))
            .group_by(Product.shop_id)
            .subquery()
        )
        rows = session.execute(
            select(Shop, func.coalesce(counts.c.n, 0))
            .outerjoin(counts, counts.c.shop_id == Shop.id)
            .order_by(Shop.floor, Shop.position)
        ).all()
        return [
            ShopSummary(slug=s.slug, name=s.name, category=s.category, floor=s.floor,
                        position=s.position, product_count=int(n))
            for s, n in rows
        ]  # fmt: skip

    @app.get("/api/shops/{slug}", response_model=ShopDetail)
    def get_shop(slug: str, session: Session = Depends(get_session)) -> ShopDetail:  # noqa: B008
        shop = session.scalar(
            select(Shop).where(Shop.slug == slug)
            .options(selectinload(Shop.products).selectinload(Product.variants))
        )  # fmt: skip
        if shop is None:
            raise HTTPException(status_code=404, detail="Shop not found")
        return ShopDetail(slug=shop.slug, name=shop.name, category=shop.category, floor=shop.floor,
                          description=shop.description,
                          products=[_product_out(p) for p in shop.products if p.active])  # fmt: skip

    @app.get("/api/products/{slug}", response_model=ProductOut)
    def get_product(slug: str, session: Session = Depends(get_session)) -> ProductOut:  # noqa: B008
        product = session.scalar(
            select(Product).where(Product.slug == slug, Product.active.is_(True))
            .options(selectinload(Product.variants))
        )  # fmt: skip
        if product is None:
            raise HTTPException(status_code=404, detail="Product not found")
        return _product_out(product)

    return app
