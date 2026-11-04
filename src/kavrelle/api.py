"""Kavrelle HTTP API (phase 0: health check and the mall directory)."""

from __future__ import annotations

import hashlib
import os
import re
from collections.abc import AsyncIterator, Awaitable, Callable, Iterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field
from sqlalchemy import Engine, func, select
from sqlalchemy.orm import Session, selectinload, sessionmaker
from starlette.exceptions import HTTPException as StarletteHTTPException

from kavrelle import auth
from kavrelle import lab as labmod
from kavrelle import mailer as mail
from kavrelle import shop as rules
from kavrelle import support as desk
from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, ping, session_scope
from kavrelle.models import (
    CONTACT_TOPICS,
    MAX_LINE_QUANTITY,
    Cart,
    ContactMessage,
    Conversation,
    Customer,
    EvalRun,
    Order,
    Product,
    Shop,
)
from kavrelle.models import Message as MessageRow
from kavrelle.models import ToolCall as ToolCallRow


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


def _public_base(request: Request) -> str:
    """The site's public address for canonical links, Open Graph and the sitemap."""
    return (os.environ.get("KAVRELLE_PUBLIC_URL") or str(request.base_url)).rstrip("/")


def _versioned_index(page: str = "index.html", base: str = "") -> str:
    """index.html with ?v=<content hash> on our own CSS/JS, so a new release is never
    hidden behind a stale browser cache. __BASE__ becomes the public site address."""
    html = (WEB_DIR / page).read_text("utf-8").replace("__BASE__", base)

    def stamp(match: re.Match[str]) -> str:
        path = match.group(1)
        f = WEB_DIR / path.removeprefix("/static/")
        digest = hashlib.sha256(f.read_bytes()).hexdigest()[:10] if f.is_file() else "0"
        return f'"{path}?v={digest}"'

    return re.sub(r'"(/static/[^"?]+\.(?:js|css))"', stamp, html)


class StartRunIn(BaseModel):
    n_runs: int = Field(default=3, ge=1, le=5)


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
    accept_terms: bool = False


class ForgotIn(BaseModel):
    email: str = Field(max_length=200)


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


class ContactIn(BaseModel):
    name: str = Field(max_length=200)
    email: str = Field(max_length=200)
    topic: str = Field(max_length=20)
    message: str = Field(max_length=5000)
    website: str = Field(default="", max_length=200)  # honeypot: people leave it empty


PAGES = {"/about": "about", "/help": "help", "/contact": "contact", "/terms": "terms",
         "/privacy": "privacy", "/reset": "reset"}  # fmt: skip


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
    evaluator: labmod.Evaluator | None = None,
    mailer: mail.Mailer | None = None,
) -> FastAPI:
    settings = settings or Settings.load()
    engine = engine or make_engine(settings.database_url)
    factory: sessionmaker[Session] = make_session_factory(engine)
    limiter = auth.RateLimiter(limit=5, window=300)
    forgot_limiter = auth.RateLimiter(limit=3, window=900)
    contact_limiter = auth.RateLimiter(limit=5, window=600)
    postman = mailer or mail.Mailer()
    support = desk.SupportDesk(factory, chat_factory or desk.ollama_chat_factory)
    lab = labmod.Lab(
        factory,
        evaluator
        or labmod.agentrel_evaluator(
            os.environ.get("KAVRELLE_SUPPORT_MODEL", "llama3.2:3b")
        ),
        label="Support Assistant v1",
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
        yield
        support.shutdown()
        postman.shutdown()

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

        @app.get("/admin", include_in_schema=False)
        def admin_page() -> HTMLResponse:
            return HTMLResponse(
                _versioned_index("admin.html"),
                headers={"Cache-Control": "no-cache", "X-Robots-Tag": "noindex"},
            )

        def _page(name: str) -> Callable[[Request], HTMLResponse]:
            def show(request: Request) -> HTMLResponse:
                return HTMLResponse(
                    _versioned_index(f"pages/{name}.html", _public_base(request)),
                    headers={"Cache-Control": "no-cache"},
                )

            return show

        for route, name in PAGES.items():
            app.add_api_route(
                route, _page(name), methods=["GET"], include_in_schema=False
            )

        @app.exception_handler(StarletteHTTPException)
        def not_found(request: Request, exc: StarletteHTTPException) -> Response:
            path = request.url.path
            if exc.status_code == 404 and not path.startswith(("/api/", "/static/")):
                return HTMLResponse(
                    _versioned_index("pages/404.html", _public_base(request)),
                    status_code=404,
                )
            return JSONResponse(
                {"detail": exc.detail}, status_code=exc.status_code, headers=exc.headers
            )

        @app.get("/", include_in_schema=False)
        def index(request: Request) -> HTMLResponse:
            return HTMLResponse(
                _versioned_index("index.html", _public_base(request)),
                headers={"Cache-Control": "no-cache"},
            )

        @app.get("/robots.txt", include_in_schema=False)
        def robots(request: Request) -> Response:
            body = ("User-agent: *\nAllow: /\nDisallow: /admin\nDisallow: /api/\nDisallow: /reset\n\n"
                    f"Sitemap: {_public_base(request)}/sitemap.xml\n")  # fmt: skip
            return Response(body, media_type="text/plain")

        @app.get("/sitemap.xml", include_in_schema=False)
        def sitemap(request: Request) -> Response:
            base = _public_base(request)
            urls = "".join(
                f"<url><loc>{base}{path}</loc><changefreq>{freq}</changefreq><priority>{prio}</priority></url>"
                for path, freq, prio in [("/", "weekly", "1.0"), ("/help", "monthly", "0.6"), ("/about", "monthly", "0.5"),
                                         ("/contact", "yearly", "0.5"), ("/terms", "yearly", "0.3"), ("/privacy", "yearly", "0.3")]
            )  # fmt: skip
            xml = f'<?xml version="1.0" encoding="UTF-8"?><urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9">{urls}</urlset>'
            return Response(xml, media_type="application/xml")

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
            out = _order_out(order)
            lines = [
                (i.name, i.variant, i.quantity, i.unit_price_cents) for i in out.items
            ]
            postman.send(mail.order_confirmation(order.customer.name, order.customer.email, order.code,
                                                 lines, order.total_cents))  # fmt: skip
            return out

    @app.post("/api/orders/{code}/lookup", response_model=OrderOut)
    def lookup_order(code: str, body: IdentityIn) -> OrderOut:
        with session_scope(factory) as s:
            return _order_out(rules.find_order(s, code, body.email, body.zip_code))

    @app.post("/api/orders/{code}/cancel", response_model=OrderOut)
    def cancel(code: str, body: IdentityIn) -> OrderOut:
        with session_scope(factory) as s:
            order = rules.cancel_order(s, code, body.email, body.zip_code)
            postman.send(
                mail.order_cancelled(
                    order.customer.name, order.customer.email, order.code
                )
            )
            return _order_out(order)

    @app.post("/api/orders/{code}/refund", response_model=OrderOut)
    def refund(code: str, body: RefundIn) -> OrderOut:
        with session_scope(factory) as s:
            order = rules.refund_order(
                s, code, body.email, body.zip_code, body.amount_cents
            )
            postman.send(mail.order_refunded(order.customer.name, order.customer.email, order.code,
                                             body.amount_cents))  # fmt: skip
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
            if not body.accept_terms:
                raise rules.Invalid(
                    "Please accept the Terms of Service and Privacy Policy"
                )
            c = auth.signup(
                s, rules.Buyer(body.name, body.email, body.zip_code), body.password
            )
            _set_cookie(response, auth.start_session(s, c))
            postman.send(mail.welcome(c.name, c.email))
            return MeOut(name=c.name, email=c.email, zip_code=c.zip_code)

    @app.post("/api/auth/forgot", status_code=202)
    def forgot(body: ForgotIn, request: Request) -> dict[str, str]:
        """Same answer whether or not the account exists, so emails can't be probed."""
        answer = {"detail": "If that email has an account, a reset link is on its way."}
        key = f"forgot:{_client(request)}:{body.email.strip().lower()}"
        if forgot_limiter.blocked(key):
            return answer
        forgot_limiter.fail(key)
        with session_scope(factory) as s:
            started = auth.request_reset(s, body.email)
            if started:
                c, token = started
                link = f"{str(request.base_url).rstrip('/')}/reset?token={token}"
                postman.send(
                    mail.password_reset(c.name, c.email, link, auth.RESET_MINUTES)
                )
        return answer

    @app.post("/api/auth/reset", status_code=204)
    def reset(body: ResetIn, response: Response) -> Response:
        with session_scope(factory) as s:
            auth.reset_password(s, body.token, body.password)
        response.delete_cookie(SESSION_COOKIE, path="/")
        response.status_code = 204
        return response

    @app.post("/api/contact", status_code=202)
    def contact(body: ContactIn, request: Request) -> dict[str, str]:
        answer = {"detail": "Thanks! We got your message and will reply by email."}
        if (
            body.website
        ):  # a bot filled the hidden field: accept silently, store nothing
            return answer
        key = f"contact:{_client(request)}"
        if contact_limiter.blocked(key):
            raise auth.RateLimited(
                "You have sent several messages already. Please try again later."
            )
        name, email, text = (
            body.name.strip(),
            body.email.strip().lower(),
            body.message.strip(),
        )
        if not 1 <= len(name) <= 80:
            raise rules.Invalid("Please enter your name")
        if not rules.EMAIL_RE.match(email) or len(email) > 160:
            raise rules.Invalid("Please enter a valid email")
        if body.topic not in CONTACT_TOPICS:
            raise rules.Invalid("Please choose a topic")
        if not 10 <= len(text) <= 3000:
            raise rules.Invalid("Please write between 10 and 3000 characters")
        contact_limiter.fail(key)
        with session_scope(factory) as s:
            s.add(
                ContactMessage(name=name, email=email, topic=body.topic, message=text)
            )
        postman.send(mail.contact_receipt(name, email, body.topic))
        inbox = os.environ.get("KAVRELLE_SUPPORT_INBOX", "")
        if inbox:
            postman.send(
                mail.Email(
                    inbox,
                    f"[Contact/{body.topic}] {name}",
                    f"From: {name} <{email}>\n\n{text}",
                )
            )
        return answer

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

    # ---------- admin console ----------
    def _admin(request: Request, s: Session) -> Customer:
        c = _me(request, s)
        if c is None:
            raise HTTPException(status_code=401, detail="Please sign in")
        if not c.is_admin:
            raise HTTPException(status_code=403, detail="Admins only")
        return c

    @app.get("/api/admin/messages")
    def admin_messages(request: Request) -> list[dict[str, Any]]:
        with session_scope(factory) as s:
            _admin(request, s)
            rows = s.scalars(
                select(ContactMessage).order_by(ContactMessage.id.desc()).limit(200)
            )
            return [{"id": m.id, "name": m.name, "email": m.email, "topic": m.topic, "message": m.message,
                     "created_at": m.created_at.isoformat()} for m in rows]  # fmt: skip

    def _require_admin(request: Request) -> None:
        with session_scope(factory) as s:
            _admin(request, s)

    @app.get("/api/admin/overview")
    def admin_overview(request: Request) -> dict[str, Any]:
        with session_scope(factory) as s:
            _admin(request, s)
            by_status = dict(
                s.execute(
                    select(Order.status, func.count()).group_by(Order.status)
                ).all()
            )
            revenue = s.scalar(
                select(func.coalesce(func.sum(Order.total_cents - Order.refunded_cents), 0))
                .where(Order.status != "cancelled")
            )  # fmt: skip
            last = s.scalar(
                select(EvalRun)
                .where(EvalRun.status == "done")
                .order_by(EvalRun.id.desc())
            )
            return {
                "orders": sum(by_status.values()),
                "orders_by_status": by_status,
                "revenue_cents": int(revenue or 0),
                "customers": s.scalar(select(func.count(Customer.id))),
                "accounts": s.scalar(
                    select(func.count(Customer.id)).where(
                        Customer.password_hash.is_not(None)
                    )
                ),
                "conversations": s.scalar(select(func.count(Conversation.id))),
                "messages": s.scalar(select(func.count(MessageRow.id))),
                "blocked_actions": s.scalar(
                    select(func.count(ToolCallRow.id)).where(ToolCallRow.ok.is_(False))
                ),
                "last_run": _run_summary(last) if last else None,
            }

    def _run_summary(r: EvalRun) -> dict[str, Any]:
        rep = (r.report or {}).get("report", {})
        return {
            "id": r.id, "name": r.name, "assistant": r.assistant_label, "status": r.status, "n_runs": r.n_runs,
            "created_at": r.created_at.isoformat(), "finished_at": r.finished_at.isoformat() if r.finished_at else None,
            "overall": rep.get("overall"), "accuracy": rep.get("accuracy"),
        }  # fmt: skip

    @app.get("/api/admin/runs")
    def admin_runs(request: Request) -> list[dict[str, Any]]:
        with session_scope(factory) as s:
            _admin(request, s)
            return [
                _run_summary(r)
                for r in s.scalars(
                    select(EvalRun).order_by(EvalRun.id.desc()).limit(50)
                )
            ]

    @app.get("/api/admin/runs/{run_id}")
    def admin_run(run_id: int, request: Request) -> dict[str, Any]:
        with session_scope(factory) as s:
            _admin(request, s)
            r = s.get(EvalRun, run_id)
            if r is None:
                raise HTTPException(status_code=404, detail="Run not found")
            rep = (r.report or {}).get("report", {})
            rules: dict[str, int] = {}
            sev: dict[str, int] = {}
            for v in r.violations:
                rules[v.rule] = rules.get(v.rule, 0) + 1
                sev[v.severity] = sev.get(v.severity, 0) + 1
            return {
                **_run_summary(r),
                "metrics": {m.key: m.value for m in r.metrics},
                "groups": {k: rep.get(k) for k in ("consistency", "predictability", "robustness", "safety")},
                "summary": (r.report or {}).get("summary", {}),
                "violations_by_rule": rules,
                "violations_by_severity": sev,
                "violations": [{"task": v.task_id, "rule": v.rule, "severity": v.severity, "detail": v.detail}
                               for v in r.violations[:300]],
            }  # fmt: skip

    @app.post("/api/admin/runs", status_code=202)
    def admin_start_run(body: StartRunIn, request: Request) -> dict[str, Any]:
        _require_admin(request)
        try:
            run_id = lab.start(body.n_runs)
        except RuntimeError as e:
            raise HTTPException(status_code=409, detail=str(e)) from e
        return {"run_id": run_id}

    @app.get("/api/admin/lab/progress")
    def admin_progress(request: Request) -> dict[str, Any]:
        _require_admin(request)
        return {"active": lab.progress()}

    @app.get("/api/admin/conversations")
    def admin_conversations(request: Request) -> list[dict[str, Any]]:
        with session_scope(factory) as s:
            _admin(request, s)
            out = []
            for c in s.scalars(
                select(Conversation).order_by(Conversation.id.desc()).limit(100)
            ):
                calls = [t for m in c.messages for t in m.tool_calls]
                who = s.get(Customer, c.customer_id) if c.customer_id else None
                first = next((m.content for m in c.messages if m.role == "user"), "")
                out.append({"id": c.public_id, "started_at": c.started_at.isoformat(),
                            "customer": who.email if who else None, "messages": len(c.messages),
                            "tool_calls": len(calls), "blocked": sum(not t.ok for t in calls),
                            "preview": first[:120]})  # fmt: skip
            return out

    @app.get("/api/admin/conversations/{public_id}")
    def admin_conversation(public_id: str, request: Request) -> dict[str, Any]:
        with session_scope(factory) as s:
            _admin(request, s)
            c = s.scalar(
                select(Conversation).where(Conversation.public_id == public_id)
            )
            if c is None:
                raise HTTPException(status_code=404, detail="Conversation not found")
            return {"id": c.public_id, "messages": [
                {"role": m.role, "content": m.content, "at": m.created_at.isoformat(),
                 "tools": [{"name": t.name, "arguments": t.arguments, "result": t.result, "ok": t.ok}
                           for t in m.tool_calls]}
                for m in c.messages]}  # fmt: skip

    app.state.support = support
    app.state.mailer = postman
    app.state.lab = lab
    return app
