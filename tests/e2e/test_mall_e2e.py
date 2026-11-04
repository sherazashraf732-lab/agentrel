"""Browser tests: a real Chromium drives the real website against a real server.

Run with:  KAVRELLE_E2E=1 uv run pytest tests/e2e -q
(needs `uv run playwright install chromium` once). Skipped otherwise.
"""

from __future__ import annotations

import os
import re
import socket
import threading
import time
from collections.abc import Iterator
from email import message_from_bytes, policy
from pathlib import Path

import pytest

if os.environ.get("KAVRELLE_E2E") != "1":
    pytest.skip("browser tests run only with KAVRELLE_E2E=1", allow_module_level=True)

import uvicorn
from playwright.sync_api import Page, expect, sync_playwright
from sqlalchemy import select, update

from agentrel.llm.ollama import ChatReply, ToolCall
from kavrelle import migrate
from kavrelle.api import create_app
from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, session_scope
from kavrelle.mailer import Mailer
from kavrelle.models import Customer, Product, Variant
from kavrelle.seed import seed


def rule_based_chat(messages: list[dict], tools: list[dict]) -> ChatReply:  # type: ignore[type-arg]
    """A tiny stand-in for the model so browser tests are exact and offline."""
    if messages[-1]["role"] == "tool":
        result = messages[-1]["content"]
        return ChatReply(content=f"Reply: {'All done.' if 'NOT ALLOWED' not in result else 'Sorry, I cannot do that.'}",
                         tool_calls=[], tokens=1)  # fmt: skip
    text = messages[-1]["content"]
    calls = []
    m = re.search(r"([\w.]+@[\w.]+)\D+(\d{5})", text)
    if m:
        calls.append(
            ToolCall(
                name="verify_customer",
                arguments={"email": m.group(1), "zip_code": m.group(2)},
            )
        )
    code = re.search(r"\b([AK][A-Z0-9]{3,8})\b", text)
    if code and "cancel" in text.lower():
        calls.append(
            ToolCall(name="cancel_order", arguments={"order_id": code.group(1)})
        )
    if calls:
        return ChatReply(content="", tool_calls=calls, tokens=1)
    return ChatReply(
        content="Reply: Happy to help! Share your email and zip code for order questions.",
        tool_calls=[],
        tokens=1,
    )


def fake_evaluator(n_runs, tick):  # type: ignore[no-untyped-def]
    """Stands in for the hour-long model evaluation in browser tests."""
    tick()
    report = {k: 0.8 for k in ["consistency_outcome", "consistency_trajectory_distribution", "consistency_trajectory_sequence",
              "consistency_resource", "robustness_fault", "robustness_env", "robustness_prompt", "predictability_calibration",
              "predictability_auroc", "predictability_brier", "safety_compliance", "safety_harm", "consistency",
              "predictability", "robustness", "safety", "overall"]}  # fmt: skip
    report["accuracy"] = 0.5
    rec = {"task_id": "status", "run_index": 0, "is_correct": True, "duration_s": 20, "tokens": 900, "confidence": 0.9,
           "violations": [{"constraint": "identity_verification", "severity": "high", "detail": "looked up A100 unverified"}]}  # fmt: skip
    return report, {"baseline": [rec], "fault": [rec]}


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
            create_app(
                Settings(),
                engine=engine,
                chat_factory=lambda: rule_based_chat,
                evaluator=fake_evaluator,
                mailer=Mailer(outbox=db.parent / "outbox"),
            ),
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


def test_switching_grid_to_3d_builds_the_mall_on_demand(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    assert page.evaluate("KV.app.state().mall") is False
    page.click('#viewSeg button[data-mode="3d"]')
    page.wait_for_function(
        "KV.app.state().mode === '3d' || KV.app.state().mall === false && document.querySelector('.toast')",
        timeout=60000,
    )
    state = page.evaluate("KV.app.state()")
    assert state["mode"] == ("3d" if state["mall"] else "2d")
    expect(page.locator("#loader")).to_have_class("done")


def test_create_account_then_see_my_orders(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.click("#acctBtn")
    page.click("#tabUp")
    page.fill("#suName", "Sara Ahmed")
    page.fill("#suEmail", "sara@example.com")
    page.fill("#suZip", "44000")
    page.fill("#suPass", "short")
    page.click("#suSubmit")
    expect(page.locator("#suPassErr")).to_contain_text("8+ characters")
    page.fill("#suPass", "monitor2026")
    page.click("#suSubmit")
    expect(page.locator("#suTermsErr")).to_contain_text("accept the Terms")
    page.check("#suTerms")
    page.click("#suSubmit")
    expect(page.locator("#acctLabel")).to_have_text("Sara")
    page.click("#acctBtn")
    expect(page.locator("#moList li")).to_have_count(2)
    page.click("#logoutBtn")
    expect(page.locator("#acctLabel")).to_have_text("Sign in")
    page.click("#acctBtn")
    page.fill("#liEmail", "sara@example.com")
    page.fill("#liPass", "wrong-pass-1")
    page.click("#liSubmit")
    expect(page.locator("#liError")).to_have_text("Email or password is incorrect")


def test_support_chat_cancels_an_order(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.click("#chatBtn")
    expect(page.locator(".bubble.bot").first).to_contain_text(
        "I can track, cancel or refund"
    )
    page.fill("#chatInput", "I'm Ali, ali@example.com zip 54000, please cancel A101")
    page.press("#chatInput", "Enter")
    expect(page.locator(".steps")).to_contain_text("Identity confirmed", timeout=20000)
    expect(page.locator(".steps")).to_contain_text("Order cancelled")
    expect(page.locator(".bubble.bot").last).to_have_text(
        "Order A101 is now cancelled."
    )  # a vague reply is replaced by the facts
    page.fill("#chatInput", "hello")
    page.press("#chatInput", "Enter")
    expect(page.locator(".bubble.bot").last).to_contain_text(
        "Happy to help", timeout=20000
    )
    page.reload()
    expect(page.locator("body")).to_have_attribute("data-ready", "true", timeout=60000)
    page.click("#chatBtn")
    expect(page.locator(".bubble.me")).to_have_count(
        2
    )  # the conversation survives a reload
    assert "llama" not in page.content().lower()


def test_support_chat_refuses_someone_elses_order(
    page: Page, server: tuple[str, str]
) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.evaluate("localStorage.removeItem('kv_chat')")
    page.click("#chatBtn")
    page.fill("#chatInput", "I'm ali@example.com zip 54000, cancel A104")
    page.press("#chatInput", "Enter")
    expect(page.locator(".bubble.bot").last).to_have_text(
        "Sorry, I cannot do that.", timeout=20000
    )


def test_admin_console_runs_an_evaluation_and_shows_12_metrics(
    page: Page, server: tuple[str, str]
) -> None:
    base, url = server
    page.goto(f"{base}/admin#lab")
    expect(page.locator("#gate")).to_be_visible()
    page.request.post(f"{base}/api/auth/signup", data={"name": "Owner", "email": "owner@example.com",
                                                        "zip_code": "54000", "password": "console2026",
                                                        "accept_terms": True})  # fmt: skip
    engine = make_engine(url)
    with session_scope(make_session_factory(engine)) as s:
        s.execute(
            update(Customer)
            .where(Customer.email == "owner@example.com")
            .values(is_admin=True)
        )
    engine.dispose()
    page.fill("#gEmail", "owner@example.com")
    page.fill("#gPass", "console2026")
    page.click("#gSubmit")
    expect(page.locator("#v-lab")).to_be_visible()
    page.click("#runStart")
    expect(page.locator(".metric")).to_have_count(12, timeout=30000)
    expect(page.locator('.metric[data-key="R_fault"] .val')).to_contain_text("0.80")
    expect(page.locator("#violTable")).to_contain_text("looked up A100 unverified")
    page.click('button[data-view="chats"]')
    expect(page.locator("#v-chats")).to_be_visible()
    page.click('button[data-view="overview"]')
    expect(page.locator(".kpi")).to_have_count(6)


# ---------- website essentials ----------


def _outbox(url: str) -> Path:
    return Path(url.removeprefix("sqlite:///")).parent / "outbox"


def test_footer_links_lead_to_real_pages(page: Page, server: tuple[str, str]) -> None:
    base, _ = server
    _open(page, base, "?mode=2d")
    page.locator(".site-foot a", has_text="Privacy").click()
    expect(page.locator("h1")).to_have_text("Privacy Policy")
    expect(page.locator(".toc a")).to_have_count(10)
    page.locator(".toc a", has_text="Cookies").click()
    expect(page.locator("#cookies")).to_be_in_viewport()
    for path, heading in [
        ("/terms", "Terms of Service"),
        ("/about", "A mall you can walk through."),
        ("/help", "How can we help?"),
    ]:
        page.goto(f"{base}{path}")
        expect(page.locator("h1")).to_have_text(heading)
    page.locator("summary", has_text="Can I cancel?").click()
    expect(page.locator("details[open] p")).to_contain_text(
        "while the order is pending"
    )
    page.goto(f"{base}/no-such-page")
    expect(page.locator("h1")).to_have_text("This shop has moved out.")
    page.click("text=Back to the mall")
    expect(page.locator("body")).to_have_attribute("data-ready", "true", timeout=60000)


def test_contact_form_validates_then_sends(page: Page, server: tuple[str, str]) -> None:
    base, url = server
    page.goto(f"{base}/contact")
    page.click("#cSubmit")
    expect(page.locator("#cNameErr")).to_have_text("Please enter your name.")
    expect(page.locator("#cTopicErr")).to_have_text("Please choose a topic.")
    page.fill("#cName", "Hina Raza")
    page.fill("#cEmail", "hina@example.com")
    page.select_option("#cTopic", "returns")
    page.fill("#cMsg", "Can I return a scarf after two weeks?")
    expect(page.locator("#cCount")).to_have_text("37 / 3000")
    page.click("#cSubmit")
    expect(page.locator("#contactDone")).to_be_visible()
    expect(page.locator("#contactForm")).to_be_hidden()
    deadline = time.time() + 10
    while time.time() < deadline and not any("We got your message" in f.read_text("utf-8", "replace")
                                             for f in _outbox(url).glob("*.eml")):  # fmt: skip
        time.sleep(0.1)
    assert any(
        "hina@example.com" in f.read_text("utf-8", "replace")
        for f in _outbox(url).glob("*.eml")
    )


def test_forgot_and_reset_password_in_the_browser(
    page: Page, server: tuple[str, str]
) -> None:
    base, url = server
    page.request.post(f"{base}/api/auth/signup", data={"name": "Nida Khan", "email": "nida@example.com",
                                                        "zip_code": "60000", "password": "garden2026",
                                                        "accept_terms": True})  # fmt: skip
    page.request.post(f"{base}/api/auth/logout")  # signing up signed this browser in
    _open(page, base, "?mode=2d")
    page.click("#acctBtn")
    page.click("#forgotLink")
    expect(page.locator("h1")).to_have_text("Reset your password")
    page.fill("#fEmail", "nida@example.com")
    page.click("#fSubmit")
    expect(page.locator("#doneTitle")).to_have_text("Check your inbox")
    link = ""
    deadline = time.time() + 10
    while time.time() < deadline and not link:
        for f in _outbox(url).glob("*.eml"):
            msg = message_from_bytes(f.read_bytes(), policy=policy.default)
            part = msg.get_body(("plain",))  # type: ignore[attr-defined]
            found = re.search(
                r"(http://127\.0\.0\.1:\d+/reset\?token=[\w-]+)",
                str(part.get_content()) if part else "",
            )
            if found and msg["To"] == "nida@example.com":
                link = found.group(1)
        time.sleep(0.1)
    assert link, "reset email with link"
    page.goto(link)
    expect(page.locator("h1")).to_have_text("Choose a new password")
    assert "token" not in page.url, "token is removed from the address bar"
    page.fill("#rPass", "garden2027")
    page.fill("#rPass2", "garden2028")
    page.click("#rSubmit")
    expect(page.locator("#rPass2Err")).to_have_text("The two passwords don’t match.")
    page.fill("#rPass2", "garden2027")
    page.click("#rSubmit")
    expect(page.locator("#doneTitle")).to_have_text("Password changed")
    r = page.request.post(
        f"{base}/api/auth/login",
        data={"email": "nida@example.com", "password": "garden2027"},
    )
    assert r.status == 200


def test_info_pages_fit_a_phone(browser, server: tuple[str, str]) -> None:  # type: ignore[no-untyped-def]
    base, _ = server
    ctx = browser.new_context(viewport={"width": 375, "height": 740})
    pg = ctx.new_page()
    pg.route("https://fonts.**", lambda r: r.abort())
    for path in [
        "/terms",
        "/privacy",
        "/help",
        "/contact",
        "/about",
        "/reset",
        "/nope",
    ]:
        pg.goto(f"{base}{path}")
        overflow = pg.evaluate(
            "document.documentElement.scrollWidth - document.documentElement.clientWidth"
        )
        assert overflow <= 0, f"{path} scrolls sideways by {overflow}px"
    ctx.close()


def test_3d_mouse_only_navigation(page: Page, server: tuple[str, str]) -> None:
    """Turn all the way round, walk out of a shop and ride the escalator using only the mouse."""
    base, _ = server
    _open(page, base, "?mode=3d")
    if not page.evaluate("KV.app.state().mall"):
        pytest.skip("no WebGL on this machine")
    page.click('#dirFloors button[data-shop="maison-elle"]')
    page.wait_for_function("() => KV.app.cam().mode === 'shop'", timeout=60000)
    box = page.locator("#scene").bounding_box()
    assert box
    cx, cy = box["x"] + box["width"] / 2, box["y"] + box["height"] / 2
    page.mouse.move(cx, cy)
    page.mouse.down()
    for i in range(1, 31):  # drag ~630px = a half turn (yaw is not clamped any more)
        page.mouse.move(cx - i * 21, cy)
    page.mouse.up()
    page.wait_for_function("() => KV.app.sign('exit') !== null", timeout=10000)
    page.wait_for_timeout(3000)  # let the camera settle
    pt = page.evaluate("KV.app.sign('exit')")
    page.mouse.move(pt["x"], pt["y"])
    expect(page.locator("#tip")).to_contain_text("Back to the concourse")
    page.mouse.click(pt["x"], pt["y"])
    expect(page.locator("#shopCard")).to_be_hidden()
    page.wait_for_function("() => KV.app.cam().mode === 'concourse'", timeout=60000)
    page.wait_for_function("() => KV.app.sign('floor') !== null", timeout=10000)
    page.wait_for_timeout(3000)
    pt = page.evaluate("KV.app.sign('floor')")
    page.mouse.click(pt["x"], pt["y"])
    page.wait_for_function(
        "() => KV.app.cam().floor === 2 && KV.app.cam().mode === 'concourse'",
        timeout=90000,
    )
    expect(page.locator('#floorSeg button[data-floor="2"]')).to_have_attribute(
        "aria-pressed", "true"
    )
