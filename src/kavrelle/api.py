"""Kavrelle HTTP API (phase 0: health check and the mall directory)."""

from __future__ import annotations

import hashlib
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker

from kavrelle import auth
from kavrelle import shop as rules
from kavrelle import support as desk
from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, ping, session_scope
from kavrelle.models import MAX_LINE_QUANTITY, Cart, Customer, Order, Product, Shop


class VariantOut(BaseModel):
    label: str
    in_stock: bool


class ProductOut(BaseModel):
    slug: str
    name: str
    description: str
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


class CatalogShop(ShopDetail):
    position: int


WEB_DIR = Path(__file__).parent / "web"
CSP = (
    "default-src 'self'; script-src 'self'; style-src 'self' https://fonts.googleapis.com; "
    "font-src 'self' https://fonts.gstatic.com; img-src 'self' data: blob:; connect-src 'self'; "
    "frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
)


def _versioned_index() -> str:
    """index.html with ?v=<content hash> on our own CSS/JS, so a new release is never
    hidden behind a stale browser cache."""
    html = (WEB_DIR / "index.html").read_text("utf-8")

    def stamp(match: re.Match[str]) -> str:
        path = match.group(1)
        f = WEB_DIR / path.removeprefix("/static/")
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:10] if f.is_file() else "0"
        return f'"{path}?v={digest}"'

    return re.sub(r'"(/static/[^"?]+\.(?:js|css))"', stamp, html)


class CartLineOut(BaseModel):
    id: int
    product_slug: str
    name: str
    variant: str
    quantity: int
    unit_price_cents: int
    line_total_cents: int


class CartOut(BaseModel):
    token: str
    items: list[CartLineOut]
    total_cents: int
    item_count: int


class AddItemIn(BaseModel):
    product_slug: str = Field(min_length=1, max_length=80)
    variant: str = Field(min_length=1, max_length=40)
    quantity: int = Field(default=1, ge=1, le=MAX_LINE_QUANTITY)


class SetQuantityIn(BaseModel):
    quantity: int = Field(ge=0, le=MAX_LINE_QUANTITY)


class BuyerIn(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=200)
    zip_code: str = Field(max_length=40)


class IdentityIn(BaseModel):
    email: str = Field(max_length=200)
    zip_code: str = Field(max_length=40)


class RefundIn(IdentityIn):
    amount_cents: int


class OrderItemOut(BaseModel):
    name: str
    variant: str
    quantity: int
    unit_price_cents: int


class OrderOut(BaseModel):
    code: str
    status: str
    total_cents: int
    refunded_cents: int
    items: list[OrderItemOut]


def _cart_out(cart: Cart) -> CartOut:
    lines = [
        CartLineOut(id=i.id, product_slug=i.variant.product.slug, name=i.variant.product.name,
                    variant=i.variant.label, quantity=i.quantity,
                    unit_price_cents=i.variant.product.price_cents,
                    line_total_cents=i.variant.product.price_cents * i.quantity)
        for i in cart.items
    ]  # fmt: skip
    return CartOut(token=cart.token, items=lines, total_cents=rules.cart_total_cents(cart),
                   item_count=sum(i.quantity for i in cart.items))  # fmt: skip


def _order_out(order: Order) -> OrderOut:
    return OrderOut(code=order.code, status=order.status, total_cents=order.total_cents,
                    refunded_cents=order.refunded_cents,
                    items=[OrderItemOut(name=i.product.name, variant=i.variant_label,
                                        quantity=i.quantity, unit_price_cents=i.unit_price_cents)
                           for i in order.items])  # fmt: skip


_STATUS = {
    "not_found": 404,
    "invalid": 422,
    "conflict": 409,
    "rate_limited": 429,
    "busy": 429,
}
SESSION_COOKIE = "kv_session"


def _product_out(p: Product) -> ProductOut:
    return ProductOut(
        slug=p.slug, name=p.name, description=p.description, color=p.color, price_cents=p.price_cents, currency=p.currency,
        variant_label=p.variant_label, image_url=p.image_url, image_credit=p.image_credit,
        variants=[VariantOut(label=v.label, in_stock=v.stock > 0) for v in p.variants],
    )  # fmt: skip


class SignupIn(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=200)
    zip_code: str = Field(max_length=40)
    password: str = Field(max_length=200)


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


class MeOut(BaseModel):
    name: str
    email: str
    zip_code: str


class MyOrderOut(BaseModel):
    code: str
    status: str
    total_cents: int
    item_count: int
    created_at: str


class ChatIn(BaseModel):
    message: str = Field(max_length=4000)
    conversation_id: str | None = Field(default=None, max_length=36)


class ChatAccepted(BaseModel):
    conversation_id: str
    job_id: str


class JobOut(BaseModel):
    status: str
    step: str
    done_steps: list[str]
    reply: str
    offline: bool


class ChatLine(BaseModel):
    role: str
    content: str


def create_app(
    settings: Settings | None = None,
    engine: Engine | None = None,
    chat_factory: Callable[[], desk.ChatFn] | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    engine = engine or make_engine(settings.database_url)
    factory: sessionmaker[Session] = make_session_factory(engine)
    limiter = auth.RateLimiter(limit=5, window=300)
    support = desk.SupportDesk(factory, chat_factory or desk.ollama_chat_factory)

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        support.shutdown()

    app = FastAPI(
        title=settings.brand,
        docs_url="/api/docs",
        openapi_url="/api/openapi.json",
        lifespan=lifespan,
    )

    def get_session() -> Iterator[Session]:
        with factory() as session:
            yield session

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("X-Frame-Options", "DENY")
        response.headers.setdefault("Referrer-Policy", "same-origin")
        response.headers.setdefault("Content-Security-Policy", CSP)
        path = request.url.path
        if path.startswith("/api/"):
            response.headers.setdefault("Cache-Control", "no-store")
        elif path.startswith(("/static/img/", "/static/vendor/")):
            response.headers.setdefault("Cache-Control", "public, max-age=604800")
        elif path.startswith("/static/"):
            response.headers.setdefault("Cache-Control", "no-cache")
        return response

    if WEB_DIR.is_dir():
        app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")

        @app.get("/", include_in_schema=False)
        def index() -> HTMLResponse:
            return HTMLResponse(
                _versioned_index(), headers={"Cache-Control": "no-cache"}
            )

    @app.get("/api/catalog", response_model=list[CatalogShop])
    def catalog_all(session: Session = Depends(get_session)) -> list[CatalogShop]:  # noqa: B008
        shops = session.scalars(
            select(Shop)
            .order_by(Shop.floor, Shop.position)
            .options(selectinload(Shop.products).selectinload(Product.variants))
        ).all()
        return [
            CatalogShop(slug=s.slug, name=s.name, category=s.category, floor=s.floor,
                        position=s.position, description=s.description,
                        products=[_product_out(p) for p in s.products if p.active])
            for s in shops
        ]  # fmt: skip

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

    @app.exception_handler(rules.ShopError)
    def shop_error(_request: Request, exc: rules.ShopError) -> JSONResponse:
        return JSONResponse({"detail": exc.message}, status_code=_STATUS[exc.kind])

    @app.post("/api/cart", response_model=CartOut, status_code=201)
    def new_cart() -> CartOut:
        with session_scope(factory) as s:
            return _cart_out(rules.create_cart(s))

    @app.get("/api/cart/{token}", response_model=CartOut)
    def read_cart(token: str) -> CartOut:
        with session_scope(factory) as s:
            return _cart_out(rules.get_cart(s, token))

    @app.post("/api/cart/{token}/items", response_model=CartOut)
    def add_to_cart(token: str, body: AddItemIn) -> CartOut:
        with session_scope(factory) as s:
            cart = rules.add_item(
                s, token, body.product_slug, body.variant, body.quantity
            )
            return _cart_out(cart)

    @app.patch("/api/cart/{token}/items/{item_id}", response_model=CartOut)
    def change_line(token: str, item_id: int, body: SetQuantityIn) -> CartOut:
        with session_scope(factory) as s:
            return _cart_out(rules.set_quantity(s, token, item_id, body.quantity))

    @app.delete("/api/cart/{token}/items/{item_id}", response_model=CartOut)
    def remove_line(token: str, item_id: int) -> CartOut:
        with session_scope(factory) as s:
            return _cart_out(rules.set_quantity(s, token, item_id, 0))

    @app.post("/api/cart/{token}/checkout", response_model=OrderOut, status_code=201)
    def checkout(token: str, body: BuyerIn) -> OrderOut:
        with session_scope(factory) as s:
            order = rules.checkout(
                s, token, rules.Buyer(body.name, body.email, body.zip_code)
            )
            return _order_out(order)

    @app.post("/api/orders/{code}/lookup", response_model=OrderOut)
    def lookup_order(code: str, body: IdentityIn) -> OrderOut:
        with session_scope(factory) as s:
            return _order_out(rules.find_order(s, code, body.email, body.zip_code))

    @app.post("/api/orders/{code}/cancel", response_model=OrderOut)
    def cancel(code: str, body: IdentityIn) -> OrderOut:
        with session_scope(factory) as s:
            return _order_out(rules.cancel_order(s, code, body.email, body.zip_code))

    @app.post("/api/orders/{code}/refund", response_model=OrderOut)
    def refund(code: str, body: RefundIn) -> OrderOut:
        with session_scope(factory) as s:
            order = rules.refund_order(
                s, code, body.email, body.zip_code, body.amount_cents
            )
            return _order_out(order)

    # ---------- accounts ----------
    def _client(request: Request) -> str:
        return request.client.host if request.client else "unknown"

    def _set_cookie(response: Response, token: str) -> None:
        response.set_cookie(SESSION_COOKIE, token, max_age=auth.SESSION_DAYS * 86400,
                            httponly=True, samesite="lax", secure=settings.secure_cookies, path="/")  # fmt: skip

    def _me(request: Request, s: Session) -> Customer | None:
        return auth.current_customer(s, request.cookies.get(SESSION_COOKIE))

    @app.post("/api/auth/signup", response_model=MeOut, status_code=201)
    def signup(body: SignupIn, response: Response) -> MeOut:
        with session_scope(factory) as s:
            c = auth.signup(
                s, rules.Buyer(body.name, body.email, body.zip_code), body.password
            )
            _set_cookie(response, auth.start_session(s, c))
            return MeOut(name=c.name, email=c.email, zip_code=c.zip_code)

    @app.post("/api/auth/login", response_model=MeOut)
    def login(body: LoginIn, request: Request, response: Response) -> MeOut:
        with session_scope(factory) as s:
            c = auth.login(s, body.email, body.password, limiter, _client(request))
            _set_cookie(response, auth.start_session(s, c))
            return MeOut(name=c.name, email=c.email, zip_code=c.zip_code)

    @app.post("/api/auth/logout", status_code=204)
    def logout(request: Request, response: Response) -> Response:
        with session_scope(factory) as s:
            auth.end_session(s, request.cookies.get(SESSION_COOKIE))
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.status_code = 204
        return response

    @app.get("/api/me", response_model=MeOut)
    def me(request: Request) -> MeOut:
        with session_scope(factory) as s:
            c = _me(request, s)
            if c is None:
                raise HTTPException(status_code=401, detail="Please sign in")
            return MeOut(name=c.name, email=c.email, zip_code=c.zip_code)

    @app.get("/api/me/orders", response_model=list[MyOrderOut])
    def my_orders(request: Request) -> list[MyOrderOut]:
        with session_scope(factory) as s:
            c = _me(request, s)
            if c is None:
                raise HTTPException(status_code=401, detail="Please sign in")
            orders = s.scalars(select(Order).where(Order.customer_id == c.id)
                               .options(selectinload(Order.items)).order_by(Order.id.desc())).all()  # fmt: skip
            return [MyOrderOut(code=o.code, status=o.status, total_cents=o.total_cents,
                               item_count=sum(i.quantity for i in o.items),
                               created_at=o.created_at.isoformat()) for o in orders]  # fmt: skip

    # ---------- support chat ----------
    @app.post("/api/support/messages", response_model=ChatAccepted, status_code=202)
    def send_message(body: ChatIn, request: Request) -> ChatAccepted:
        with session_scope(factory) as s:
            c = _me(request, s)
            customer_id = c.id if c else None
        job = support.submit(body.message, body.conversation_id, customer_id)
        return ChatAccepted(conversation_id=job.conversation, job_id=job.id)

    @app.get("/api/support/jobs/{job_id}", response_model=JobOut)
    def job_status(job_id: str) -> JobOut:
        job = support.job(job_id)
        if job is None:
            raise HTTPException(status_code=404, detail="That reply has expired")
        return JobOut(status=job.status, step=job.step, done_steps=list(job.done_steps),
                      reply=job.reply, offline=job.offline)  # fmt: skip

    @app.get(
        "/api/support/conversations/{conversation_id}", response_model=list[ChatLine]
    )
    def conversation(conversation_id: str) -> list[ChatLine]:
        return [ChatLine(**m) for m in support.history(conversation_id)]

    app.state.support = support
    return app
