"""Browser tests: a real Chromium drives the real website against a real server.

Run with:  KAVRELLE_E2E=1 uv run pytest tests/e2e -q
(needs `uv run playwright install chromium` once). Skipped otherwise.
"""

from __future__ import annotations

import os
import socket
import threading
import time
from collections.abc import Iterator
from pathlib import Path

import pytest

if os.environ.get("KAVRELLE_E2E") != "1":
    pytest.skip("browser tests run only with KAVRELLE_E2E=1", allow_module_level=True)

import uvicorn
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select, update

from kavrelle import migrate
from kavrelle.api import create_app
from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, session_scope
from kavrelle.models import Product, Variant
from kavrelle.seed import seed

BROWSER_ARGS = [
    "--enable-unsafe-swiftshader",
    "--use-gl=angle",
    "--use-angle=swiftshader",
    "--ignore-gpu-blocklist",
]


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@pytest.fixture(scope="module")
def server(tmp_path_factory: pytest.TempPathFactory) -> Iterator[tuple[str, str]]:
    db = tmp_path_factory.mktemp("e2e") / "mall.db"
    url = f"sqlite:///{db.as_posix()}"
    migrate.upgrade(url)
    engine = make_engine(url)
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
        # one sold-out size, one product with a hostile name (must render as plain text)
        v = s.scalar(
            select(Variant)
            .join(Product)
            .where(Product.slug == "street-sneaker", Variant.label == "44")
        )
        assert v is not None
        v.stock = 0
        s.execute(update(Product).where(Product.slug == "match-football")
                  .values(name='<img src=x onerror="window.__xss=1">Ball'))  # fmt: skip
    port = _free_port()
    srv = uvicorn.Server(
        uvicorn.Config(
            create_app(Settings(), engine=engine),
            host="127.0.0.1",
            port=port,
            log_level="warning",
        )
    )
    t = threading.Thread(target=srv.run, daemon=True)
    t.start()
    for _ in range(100):
        if srv.started:
            break
        time.sleep(0.05)
    yield f"http://127.0.0.1:{port}", url
    srv.should_exit = True
    t.join(timeout=5)
    engine.dispose()


@pytest.fixture(scope="module")
def browser() -> Iterator[object]:
    with sync_playwright() as p:
        b = p.chromium.launch(args=BROWSER_ARGS)
        yield b
        b.close()


@pytest.fixture
def page(browser, server: tuple[str, str]) -> Iterator[Page]:  # type: ignore[no-untyped-def]
    ctx = browser.new_context(viewport={"width": 1366, "height": 768})
    pg = ctx.new_page()
    pg.route("https://fonts.**", lambda r: r.abort())  # offline-safe: system fonts
    errors: list[str] = []
    pg.on("pageerror", lambda e: errors.append(str(e)))
    pg.errors = errors  # type: ignore[attr-defined]
    yield pg
    ctx.close()
    assert errors == [], f"uncaught page errors: {errors}"


def _open(page: Page, base: str, query: str = "") -> None:
    page.goto(f"{base}/{query}")
    expect(page.locator("body")).to_have_attribute("data-ready", "true", timeout=60000)


def test_full_purchase_journey_in_grid_mode(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.click('[data-product="black-evening-dress"]')
    expect(page.locator("#productSheet")).to_have_class("sheet open")
    page.click("#addBtn")  # no size chosen yet
    expect(page.locator("#pError")).to_contain_text("choose a size")
    page.click('#pChips button:has-text("M")')
    page.click("#qPlus")
    expect(page.locator("#pTotal")).to_have_text("$378.00")
    page.click("#addBtn")
    expect(page.locator("#cartCount")).to_have_text("2")
    page.click("#cartBtn")
    expect(page.locator(".cart-line")).to_have_count(1)
    page.click('.cart-line button[aria-label^="One less"]')
    expect(page.locator("#cartTotal")).to_have_text("$189.00")
    page.click("#checkoutBtn")
    page.fill("#coName", "Zara Malik")
    page.fill("#coEmail", "not-an-email")
    page.fill("#coZip", "75500")
    page.click("#coSubmit")
    expect(page.locator("#coEmailErr")).to_have_text("Please enter a valid email.")
    page.fill("#coEmail", "zara@example.com")
    page.click("#coSubmit")
    expect(page.locator("#coDone")).to_be_visible()
    code = page.locator("#coCode").inner_text()
    assert code.startswith("K") and len(code) == 8
    expect(page.locator("#cartCount")).to_have_text("0")
    page.click("#coTrack")
    page.click("#trSubmit")
    expect(page.locator("#trStatus")).to_have_text("pending")
    page.click("#trCancel")
    page.click("#trYes")
    expect(page.locator("#trStatus")).to_have_text("cancelled")
    expect(page.locator("#trCancel")).to_be_hidden()


def test_wrong_details_never_reveal_an_order(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.click("#trackBtn")
    page.fill("#trCode", "A104")  # Sara's order
    page.fill("#trEmail", "ali@example.com")
    page.fill("#trZip", "54000")
    page.click("#trSubmit")
    expect(page.locator("#trError")).to_contain_text("couldn’t find an order")


def test_sold_out_size_cannot_be_chosen(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.click('[data-product="street-sneaker"]')
    expect(page.locator('#pChips button:has-text("44")')).to_be_disabled()
    expect(page.locator('#pChips button:has-text("42")')).to_be_enabled()


def test_hostile_product_names_are_shown_as_text(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    expect(page.locator('[data-product="match-football"] .n')).to_contain_text(
        "<img src=x"
    )
    assert page.evaluate("window.__xss") is None


def test_search_finds_and_opens_a_product(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.fill("#q", "lilac satin")
    expect(page.locator("#qResults li")).to_have_count(1)
    page.keyboard.press("Enter")
    expect(page.locator("#pName")).to_have_text(
        "Lilac Ruched Satin Dress", timeout=5000
    )


def test_server_down_is_handled_gracefully(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.click('[data-product="silk-scarf"]')
    page.route("**/api/**", lambda r: r.abort())
    page.click("#addBtn")
    expect(page.locator("#pError")).to_contain_text("Can’t reach the mall")
    expect(page.locator("#offline")).to_be_visible()
    page.unroute("**/api/**")
    page.click("#offlineRetry")
    expect(page.locator("#offline")).to_be_hidden()


def test_catalog_failure_shows_retry_then_recovers(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    page.route("**/api/catalog", lambda r: r.fulfill(status=500, body="boom"))
    page.goto(f"{base}/?mode=2d")
    expect(page.locator("#loadError")).to_be_visible(timeout=20000)
    page.unroute("**/api/catalog")
    page.click("#loadRetry")
    expect(page.locator("body")).to_have_attribute("data-ready", "true", timeout=30000)


def test_3d_mall_walks_into_a_shop(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=3d")
    state = page.evaluate("KV.app.state()")
    if not state[
        "mall"
    ]:  # a machine without WebGL must fall back to the grid, never break
        assert state["mode"] == "2d"
        expect(page.locator("#grid2d")).to_be_visible()
        return
    expect(page.locator("#scene")).to_be_visible()
    page.click('#dirFloors button[data-shop="maison-elle"]')
    expect(page.locator("#shopName")).to_have_text("Maison Elle")
    page.evaluate("KV.app.openProduct('purple-satin-dress')")
    expect(page.locator("#pPrice")).to_have_text("$145.00")
    page.keyboard.press("Escape")
    page.locator("#scene").focus()
    page.keyboard.press("Escape")
    expect(page.locator("#shopCard")).to_be_hidden()


def test_mobile_gets_the_grid_without_sideways_scroll(
    browser, server: tuple[str, str]
) -> None:  # type: ignore[no-untyped-def]
    base, _ = server
    ctx = browser.new_context(
        viewport={"width": 390, "height": 844}, is_mobile=True, has_touch=True
    )
    pg = ctx.new_page()
    pg.route("https://fonts.**", lambda r: r.abort())
    _open(pg, base)
    assert pg.evaluate("KV.app.state().mode") == "2d"
    assert pg.evaluate("document.documentElement.scrollWidth <= window.innerWidth + 1")
    ctx.close()


def test_page_has_no_vendor_names_once_rendered(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    text = page.content().lower()
    for word in ["ollama", "qwen", "llama", "openai"]:
        assert word not in text


def test_screens_are_saved_for_review(
    page: Page, server: tuple[str, str], tmp_path: Path
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    out = Path(os.environ.get("KAVRELLE_E2E_SHOTS", tmp_path))
    out.mkdir(parents=True, exist_ok=True)
    page.screenshot(path=str(out / "grid.png"))
    assert (out / "grid.png").stat().st_size > 10_000
