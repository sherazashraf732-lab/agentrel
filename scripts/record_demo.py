"""Record a narrated, self-checking demo of the whole Kavrelle website.

Runs against a live server (default http://127.0.0.1:8000) and records one video with:
3D mall (360° look, walking, shops, products, exit sign, escalator), grid view, sign-up,
cart and checkout, order tracking, Kavrelle Support chat, help/legal/contact pages,
password reset page and the admin console with the 12 reliability metrics.

Every step is verified while recording; captions show ✓ or ✗, and a summary of all checks
is the last scene and is also written to checks.txt. The admin login is read from a local
file and typed into a password field, so it never appears on screen.

    uv run python scripts/record_demo.py --login agentrel-results/kavrelle-owner-login.txt
"""

from __future__ import annotations

import argparse
import html
import re
import shutil
import time
from pathlib import Path

from playwright.sync_api import Page, expect, sync_playwright

W, H = 1280, 720

OVERLAY = """
(() => {
  const boot = () => {
    if (document.getElementById('__demo_cursor')) return;
    const c = document.createElement('div'); c.id = '__demo_cursor';
    Object.assign(c.style, {position:'fixed', left:'0', top:'0', width:'22px', height:'22px', marginLeft:'-11px',
      marginTop:'-11px', borderRadius:'50%', background:'rgba(226,85,45,.35)', border:'2px solid #e2552d',
      zIndex:'2147483647', pointerEvents:'none', transition:'transform .08s'});
    const cap = document.createElement('div'); cap.id = '__demo_caption';
    Object.assign(cap.style, {position:'fixed', left:'50%', bottom:'78px', transform:'translateX(-50%)', maxWidth:'82%',
      padding:'10px 18px', borderRadius:'14px', background:'rgba(31,26,22,.88)', color:'#fff',
      font:'600 17px/1.35 Inter, system-ui, sans-serif', zIndex:'2147483646', pointerEvents:'none',
      textAlign:'center', boxShadow:'0 10px 30px rgba(0,0,0,.25)'});
    cap.hidden = true;
    document.body.append(c, cap);
    addEventListener('mousemove', (e) => { c.style.left = e.clientX + 'px'; c.style.top = e.clientY + 'px'; }, true);
    addEventListener('mousedown', () => { c.style.transform = 'scale(.7)'; }, true);
    addEventListener('mouseup', () => { c.style.transform = ''; }, true);
  };
  if (document.body) boot(); else addEventListener('DOMContentLoaded', boot);
})();
"""


class Demo:
    def __init__(self, page: Page, base: str) -> None:
        self.page, self.base = page, base
        self.checks: list[tuple[bool, str]] = []
        self.mx, self.my = W / 2, H / 2

    # ---------- presentation ----------
    def say(self, text: str, hold: float = 1.6) -> None:
        self.page.evaluate(
            """(t) => { const c = document.getElementById('__demo_caption');
                       if (c) { c.textContent = t; c.hidden = !t; } }""",
            text,
        )
        time.sleep(hold)

    def check(self, label: str, fn) -> bool:  # type: ignore[no-untyped-def]
        try:
            fn()
            ok = True
        except Exception as e:  # noqa: BLE001 - a failed check is recorded, the demo goes on
            ok = False
            print(f"   ✗ {label}: {str(e).splitlines()[0][:160]}")
        self.checks.append((ok, label))
        print(f"{'✓' if ok else '✗'} {label}")
        self.say(f"{'✓' if ok else '✗'} {label}", 1.4)
        return ok

    def move(self, x: float, y: float, steps: int = 18) -> None:
        self.page.mouse.move(x, y, steps=steps)
        self.mx, self.my = x, y

    def click_at(self, x: float, y: float) -> None:
        self.move(x, y)
        time.sleep(0.25)
        self.page.mouse.click(x, y)

    def click(self, selector: str) -> None:
        loc = self.page.locator(selector).first
        loc.scroll_into_view_if_needed()
        box = loc.bounding_box()
        if box:
            self.move(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
            time.sleep(0.2)
        loc.click()

    def type(self, selector: str, text: str, delay: int = 45) -> None:
        self.click(selector)
        self.page.locator(selector).fill("")
        self.page.locator(selector).type(text, delay=delay)

    def drag(self, dx: float, dy: float = 0, steps: int = 60) -> None:
        x0, y0 = W / 2, H / 2 + 60
        self.move(x0, y0, 8)
        self.page.mouse.down()
        for i in range(1, steps + 1):
            self.page.mouse.move(x0 + dx * i / steps, y0 + dy * i / steps)
            time.sleep(0.02)
        self.page.mouse.up()

    def goto(self, path: str) -> None:
        self.page.goto(self.base + path)

    def wait_js(self, expr: str, timeout: int = 60000) -> None:
        self.page.wait_for_function(f"() => {expr}", timeout=timeout)


def run(base: str, login_file: Path, out: Path, headed: bool = False) -> int:
    text = login_file.read_text("utf-8")
    admin_email = re.search(r"[\w.+-]+@[\w.-]+\.\w+", text)
    admin_pw = re.search(r"(?im)password\s*[:=]\s*(\S+)", text)
    if not admin_email or not admin_pw:
        raise SystemExit(
            f"Could not read the admin email and password from {login_file}"
        )
    stamp = time.strftime("%H%M%S")
    shopper = {
        "name": "Areeba Khan",
        "email": f"areeba.demo{stamp}@example.com",
        "zip": "54000",
        "pw": f"Demo{stamp}x",
    }
    out.mkdir(parents=True, exist_ok=True)
    raw = out / "_raw"
    shutil.rmtree(raw, ignore_errors=True)

    with sync_playwright() as p:
        browser = p.chromium.launch(
            headless=not headed,
            channel="chrome" if headed else None,
            args=[
                "--ignore-gpu-blocklist",
                "--enable-gpu-rasterization",
                f"--window-size={W},{H + 140}",
            ],
        )
        ctx = browser.new_context(viewport={"width": W, "height": H}, record_video_dir=str(raw),
                                  record_video_size={"width": W, "height": H})  # fmt: skip
        ctx.add_init_script(OVERLAY)
        page = ctx.new_page()
        page.set_default_timeout(30000)
        d = Demo(page, base)

        # ---------- 1. the 3D mall ----------
        d.goto("/?mode=3d")
        expect(page.locator("body")).to_have_attribute(
            "data-ready", "true", timeout=120000
        )
        has3d = page.evaluate("KV.app.state().mall")
        d.say(
            "Kavrelle — a 3D shopping mall. Two floors, twelve shops, real product photos.",
            3,
        )
        d.check(
            "Mall opens in 3D",
            lambda: (
                (_ for _ in ()).throw(AssertionError("no WebGL")) if not has3d else None
            ),
        )
        if has3d:
            d.say("Drag with the mouse to look around — a full 360° turn.", 1.2)
            d.drag(-1300, 0, 140)
            d.drag(-1250, 0, 140)
            d.say("Scroll to walk down the concourse.", 0.8)
            d.move(W / 2, H / 2)
            for _ in range(5):
                page.mouse.wheel(0, -120)
                time.sleep(0.45)
            d.check(
                "Walking moves the camera",
                lambda: d.wait_js("KV.app.cam().mode === 'concourse'"),
            )

            d.say("Click a shop to walk in: Maison Elle.", 1.0)
            d.click('#dirFloors button[data-shop="maison-elle"]')
            d.wait_js("KV.app.cam().mode === 'shop'", 60000)
            time.sleep(1.5)
            d.check(
                "Walked into Maison Elle",
                lambda: expect(page.locator("#shopName")).to_have_text("Maison Elle"),
            )
            d.say(
                "Turn around inside the shop — then click the EXIT sign by the door.",
                1.0,
            )
            d.drag(-640, 0, 90)
            d.wait_js("KV.app.sign('exit') !== null", 20000)
            time.sleep(2)
            pt = page.evaluate("KV.app.sign('exit')")
            d.move(pt["x"], pt["y"], 25)
            time.sleep(0.8)
            d.click_at(pt["x"], pt["y"])
            d.check("EXIT sign walks back to the concourse (mouse only)",
                    lambda: d.wait_js("KV.app.cam().mode === 'concourse'", 60000))  # fmt: skip
            time.sleep(1.5)
            d.say("Click the escalator sign to ride up to Floor 2.", 1.0)
            if page.evaluate("KV.app.sign('floor')") is None:
                d.drag(500, 0, 60)  # face the escalator
            d.wait_js("KV.app.sign('floor') !== null", 20000)
            time.sleep(1.5)
            pt = page.evaluate("KV.app.sign('floor')")
            d.move(pt["x"], pt["y"], 25)
            time.sleep(0.8)
            d.click_at(pt["x"], pt["y"])
            d.check("Escalator ride to Floor 2",
                    lambda: d.wait_js("KV.app.cam().floor === 2 && KV.app.cam().mode === 'concourse'", 90000))  # fmt: skip
            time.sleep(2)
            d.drag(-500, 0, 60)
            d.say("Floor 2: books, sports, kids, jewellery and the food court.", 1.5)
            d.click('#dirFloors button[data-shop="sports-hub"]')
            d.wait_js("KV.app.cam().mode === 'shop'", 60000)
            time.sleep(1.5)
            d.say("Scroll back to step out of a shop.", 0.8)
            d.move(W / 2, H / 2)
            page.mouse.wheel(0, 200)
            d.check(
                "Scrolling back leaves the shop",
                lambda: d.wait_js("KV.app.cam().mode === 'concourse'", 60000),
            )
            time.sleep(1)

        # ---------- 2. a product ----------
        d.say("Open a product: photo, price, sizes and stock.", 1.0)
        page.evaluate("KV.app.openShop('stride-lab')")
        time.sleep(2.5)
        page.evaluate("KV.app.openProduct('street-sneaker')")
        expect(page.locator("#productSheet")).to_have_class(re.compile("open"))
        time.sleep(1.2)
        d.click("#pChips button:not([disabled]) >> nth=1")
        d.click("#qPlus")
        d.check("Product sheet shows price and total",
                lambda: expect(page.locator("#pTotal")).to_contain_text("$278.00"))  # fmt: skip
        d.click("#addBtn")
        d.check(
            "Added to cart",
            lambda: expect(page.locator("#cartCount")).to_have_text("2"),
        )
        time.sleep(1)

        # ---------- 3. grid view ----------
        d.say("Prefer a classic shop? Switch to the Grid view.", 1.0)
        d.click('#viewSeg button[data-mode="2d"]')
        d.check(
            "Grid view lists every shop",
            lambda: expect(page.locator("#grid2d .shop-sec")).to_have_count(12),
        )
        for _ in range(6):
            page.mouse.wheel(0, 650)
            time.sleep(0.6)
        d.check(
            "Footer links on the grid",
            lambda: expect(page.locator(".site-foot a")).to_have_count(5),
        )
        page.mouse.wheel(0, -20000)
        time.sleep(0.8)

        # ---------- 4. sign up ----------
        d.say("Create an account. Terms must be accepted first.", 1.0)
        d.click("#acctBtn")
        d.click("#tabUp")
        d.type("#suName", shopper["name"])
        d.type("#suEmail", shopper["email"], 25)
        d.type("#suZip", shopper["zip"])
        d.type("#suPass", shopper["pw"])
        d.click("#suSubmit")
        d.check("Sign-up refused until Terms are accepted",
                lambda: expect(page.locator("#suTermsErr")).to_contain_text("accept the Terms"))  # fmt: skip
        d.click("#suTerms")
        d.click("#suSubmit")
        d.check(
            "Account created and signed in",
            lambda: expect(page.locator("#acctLabel")).to_have_text("Areeba"),
        )
        time.sleep(1)

        # ---------- 5. checkout ----------
        d.say("Checkout: the cart is kept, details are filled from the account.", 1.0)
        d.click("#cartBtn")
        time.sleep(1.2)
        d.click("#checkoutBtn")
        time.sleep(0.8)
        d.click("#coSubmit")
        d.check(
            "Order placed with a tracking code",
            lambda: expect(page.locator("#coCode")).to_have_text(
                re.compile(r"^[A-Z0-9]{6,12}$")
            ),
        )
        code = page.locator("#coCode").inner_text().strip()
        d.say(f"Order code {code} — a confirmation email is sent too.", 2.2)
        d.click("#coTrack")
        time.sleep(0.6)
        if page.locator("#trackForm").is_visible():
            if not page.locator("#trCode").input_value():
                d.type("#trCode", code)
            if not page.locator("#trEmail").input_value():
                d.type("#trEmail", shopper["email"], 20)
            if not page.locator("#trZip").input_value():
                d.type("#trZip", shopper["zip"])
            d.click("#trSubmit")
        d.check(
            "Order tracking shows it as pending",
            lambda: expect(page.locator("#trStatus")).to_contain_text(
                re.compile("pending", re.IGNORECASE)
            ),
        )
        time.sleep(1.2)
        page.keyboard.press("Escape")
        time.sleep(0.5)

        # ---------- 6. Kavrelle Support ----------
        d.say(
            "Ask Kavrelle: the support assistant checks the real order before answering.",
            1.5,
        )
        d.click("#chatBtn")
        q1 = f"Where is my order {code}?"
        d.type("#chatInput", q1, 35)
        d.click("#chatSend")
        d.check("Support answers about the order",
                lambda: expect(page.locator("#chatLog .bubble.bot").nth(1)).to_be_visible(timeout=360000))  # fmt: skip
        time.sleep(3)
        reply1 = page.locator("#chatLog .bubble.bot").nth(1).inner_text()
        d.check("The answer is about this order", lambda: (_ for _ in ()).throw(AssertionError(reply1))
                if not (code in reply1 or "pending" in reply1.lower() or "placed" in reply1.lower()) else None)  # fmt: skip
        q2 = f"Please cancel order {code}."
        d.type("#chatInput", q2, 35)
        d.click("#chatSend")
        d.check("Support replies to the cancel request",
                lambda: expect(page.locator("#chatLog .bubble.bot").nth(2)).to_be_visible(timeout=360000))  # fmt: skip
        time.sleep(3)
        steps = page.locator("#chatLog .steps").all_inner_texts()
        print("   chat steps:", steps)
        status = page.evaluate("""async ([c, e, z]) => {
            const r = await fetch(`/api/orders/${c}/lookup`, {method: 'POST', headers: {'Content-Type': 'application/json'},
              body: JSON.stringify({email: e, zip_code: z})}); return (await r.json()).status; }""",
                               [code, shopper["email"], shopper["zip"]])  # fmt: skip
        d.check(f"Order really cancelled in the database (status: {status})",
                lambda: (_ for _ in ()).throw(AssertionError(status)) if status != "cancelled" else None)  # fmt: skip
        d.click("#chatClose")

        # ---------- 7. my orders ----------
        d.click("#acctBtn")
        d.check(
            "My orders shows the order",
            lambda: expect(page.locator("#moList")).to_contain_text(code),
        )
        time.sleep(1.5)
        page.keyboard.press("Escape")

        # ---------- 8. information pages ----------
        d.say(
            "Every store page a customer expects: Help, Contact, Terms, Privacy.", 1.0
        )
        d.goto("/help")
        d.click("summary:has-text('Can I cancel?')")
        d.check(
            "Help & FAQ answers open",
            lambda: expect(page.locator("details[open] p")).to_contain_text("pending"),
        )
        time.sleep(1)
        d.goto("/terms")
        d.check(
            "Terms of Service",
            lambda: expect(page.locator("h1")).to_have_text("Terms of Service"),
        )
        page.mouse.wheel(0, 900)
        time.sleep(1.2)
        d.goto("/privacy")
        d.click(".toc a:has-text('Cookies')")
        d.check(
            "Privacy Policy",
            lambda: expect(page.locator("#cookies")).to_be_in_viewport(),
        )
        time.sleep(1.2)
        d.goto("/contact")
        d.click("#cSubmit")
        d.check(
            "Contact form checks every field",
            lambda: expect(page.locator("#cNameErr")).to_have_text(
                "Please enter your name."
            ),
        )
        d.type("#cName", shopper["name"])
        d.type("#cEmail", shopper["email"], 20)
        page.select_option("#cTopic", "returns")
        d.type(
            "#cMsg", "Hi! Can I return sneakers after two weeks if they are unworn?", 25
        )
        d.click("#cSubmit")
        d.check(
            "Contact message sent",
            lambda: expect(page.locator("#contactDone")).to_be_visible(),
        )
        time.sleep(1)
        d.goto("/reset")
        d.say(
            "Forgot your password? A single-use link is emailed, valid for 30 minutes.",
            2.2,
        )
        d.goto("/this-page-does-not-exist")
        d.check(
            "Friendly 404 page",
            lambda: expect(page.locator("h1")).to_have_text("This shop has moved out."),
        )

        # ---------- 9. admin console ----------
        d.say(
            "The owner's console. The admin password is typed from a private file, never shown.",
            1.5,
        )
        page.request.post(f"{base}/api/auth/logout")
        d.goto("/admin#overview")
        page.locator("#gEmail").fill(admin_email.group(0))
        page.locator("#gPass").fill(admin_pw.group(1))
        d.click("#gSubmit")
        d.check(
            "Admin signs in",
            lambda: expect(page.locator("#kpis .kpi")).to_have_count(6),
        )
        time.sleep(2)
        d.click('button[data-view="lab"]')
        d.say(
            "Reliability Lab: the support assistant scored on the 12 metrics of the Princeton paper.",
            1.5,
        )
        d.check(
            "All 12 reliability metrics shown",
            lambda: expect(page.locator(".metric")).to_have_count(12, timeout=30000),
        )
        for _ in range(5):
            page.mouse.wheel(0, 600)
            time.sleep(1.3)
        d.click('button[data-view="chats"]')
        time.sleep(1.5)
        d.click("#chatList button >> nth=0")
        d.check("Every step of our chat is audited",
                lambda: expect(page.locator("#chatDetail")).to_contain_text(code))  # fmt: skip
        time.sleep(2.5)
        d.click('button[data-view="inbox"]')
        d.check(
            "Contact message reached the inbox",
            lambda: expect(page.locator("#inboxList")).to_contain_text(
                "return sneakers"
            ),
        )
        time.sleep(2)

        # ---------- summary ----------
        ok = sum(1 for good, _ in d.checks if good)
        rows = "".join(
            f"<li class={'ok' if g else 'bad'}>{'✓' if g else '✗'} {html.escape(lbl)}</li>"
            for g, lbl in d.checks
        )
        page.goto(
            "about:blank"
        )  # leave the site so its CSP does not apply to the summary card
        page.set_content(f"""<html><body style="margin:0;background:#f6ecdf;font:16px/1.4 Inter,system-ui,sans-serif;color:#1f1a16">
          <div style="padding:34px 56px"><div style="font:900 34px/1 system-ui">Kavrelle demo — {ok}/{len(d.checks)} checks passed</div>
          <ul style="columns:2;gap:40px;padding:0;list-style:none;margin-top:22px;font-size:15px">{rows}</ul></div>
          <style>li{{margin:0 0 7px}} .ok{{color:#2f7d4f}} .bad{{color:#b3261e;font-weight:700}}</style></body></html>""")
        time.sleep(6)
        video = page.video
        ctx.close()
        browser.close()
        assert video is not None
        final = out / "kavrelle-demo.webm"
        Path(video.path()).replace(final)
        shutil.rmtree(raw, ignore_errors=True)

    (out / "checks.txt").write_text(
        "\n".join(f"{'PASS' if g else 'FAIL'}  {lbl}" for g, lbl in d.checks) + "\n",
        "utf-8",
    )
    print(f"\n{ok}/{len(d.checks)} checks passed. Video: {final}")
    return 0 if ok == len(d.checks) else 1


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--base", default="http://127.0.0.1:8000")
    ap.add_argument(
        "--login", type=Path, default=Path("agentrel-results/kavrelle-owner-login.txt")
    )
    ap.add_argument("--out", type=Path, default=Path("agentrel-results/demo"))
    ap.add_argument(
        "--headed",
        action="store_true",
        help="show the browser window (uses the real GPU: much faster 3D)",
    )
    a = ap.parse_args()
    raise SystemExit(run(a.base.rstrip("/"), a.login, a.out, a.headed))
