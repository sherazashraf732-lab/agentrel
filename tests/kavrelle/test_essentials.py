"""Website essentials: legal and help pages, emails, password reset, contact form, 404s."""

from __future__ import annotations

import re
import smtplib
import socket
from datetime import UTC, datetime, timedelta
from email import message_from_bytes, policy
from email.message import EmailMessage
from pathlib import Path
from typing import Self

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, func, select, update

from kavrelle import mailer as mail
from kavrelle.api import create_app
from kavrelle.config import Settings
from kavrelle.db import make_session_factory, session_scope
from kavrelle.models import AuthSession, ContactMessage, Customer, PasswordReset
from kavrelle.seed import seed

ZARA = {"name": "Zara Malik", "email": "zara@example.com", "zip_code": "75500",
        "password": "silk2026scarf", "accept_terms": True}  # fmt: skip
ALI = {"email": "ali@example.com", "zip_code": "54000"}
MSG = {"name": "Hina", "email": "hina@example.com", "topic": "returns",
       "message": "Can I return a scarf after two weeks?"}  # fmt: skip
PAGES = ["/about", "/help", "/contact", "/terms", "/privacy", "/reset"]
VENDOR_WORDS = re.compile(
    r"llama|ollama|\bmeta\b|openai|\bgpt|claude|anthropic|gemini|mistral", re.IGNORECASE
)


@pytest.fixture
def outbox(tmp_path: Path) -> Path:
    return tmp_path / "mail"


@pytest.fixture
def mailer(outbox: Path) -> mail.Mailer:
    return mail.Mailer(outbox=outbox)


@pytest.fixture
def engine_seeded(engine: Engine) -> Engine:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    return engine


@pytest.fixture
def client(engine_seeded: Engine, mailer: mail.Mailer) -> TestClient:
    return TestClient(create_app(Settings(), engine=engine_seeded, mailer=mailer))


def inbox(mailer: mail.Mailer, outbox: Path) -> list[EmailMessage]:
    mailer.flush()
    files = sorted(outbox.glob("*.eml")) if outbox.exists() else []
    return [message_from_bytes(f.read_bytes(), policy=policy.default) for f in files]  # type: ignore[misc]


def body(m: EmailMessage) -> str:
    part = m.get_body(("plain",))
    assert part is not None
    return str(part.get_content())


def count(engine: Engine, model: type) -> int:
    with session_scope(make_session_factory(engine)) as s:
        return int(s.scalar(select(func.count()).select_from(model)) or 0)


# ---------- pages ----------


@pytest.mark.parametrize("path", PAGES)
def test_every_info_page_is_served_safely(client: TestClient, path: str) -> None:
    r = client.get(path)
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert "default-src 'self'" in r.headers["content-security-policy"]
    html = r.text
    assert "<title>" in html and "| Kavrelle</title>" in html
    for link in ("/terms", "/privacy", "/help", "/contact", "/about"):
        assert f'href="{link}"' in html, f"{path} footer is missing {link}"
    assert re.search(r'/static/pages\.css\?v=[0-9a-f]{10}"', html), (
        "cache-busting stamp"
    )
    assert "<script>" not in html and " style=" not in html, "CSP forbids inline code"
    visible = re.sub(r"<[^>]+>", " ", html)
    assert not VENDOR_WORDS.search(visible), "no model or vendor names on the site"


def test_legal_pages_say_what_matters(client: TestClient) -> None:
    terms, privacy = client.get("/terms").text, client.get("/privacy").text
    assert (
        "Effective November 5, 2026" in terms
        and "Effective November 5, 2026" in privacy
    )
    assert "no payment is collected" in terms and "pending" in terms.lower()
    assert "kv_session" in privacy and "no analytics" in privacy.lower()
    assert "do not sell" in privacy and "30 days" in privacy


def test_unknown_pages_get_a_friendly_404_and_api_stays_json(
    client: TestClient,
) -> None:
    r = client.get("/no-such-page")
    assert r.status_code == 404 and "This shop has moved out" in r.text
    assert r.headers["content-type"].startswith("text/html")
    r = client.get("/api/no-such-thing")
    assert r.status_code == 404 and r.json() == {"detail": "Not Found"}
    assert (
        client.get("/api/me").status_code == 401
    )  # other HTTP errors keep their JSON shape


def test_static_assets_for_pages_exist(client: TestClient) -> None:
    assert client.get("/static/pages.css").status_code == 200
    js = client.get("/static/js/pages.js")
    assert (
        js.status_code == 200
        and "/api/contact" in js.text
        and "innerHTML" not in js.text
    )


# ---------- sign-up consent and welcome ----------


def test_signup_requires_accepting_the_terms(
    client: TestClient, engine_seeded: Engine
) -> None:
    before = count(engine_seeded, Customer)
    r = client.post("/api/auth/signup", json={**ZARA, "accept_terms": False})
    assert r.status_code == 422 and "Terms" in r.json()["detail"]
    r = client.post(
        "/api/auth/signup", json={k: v for k, v in ZARA.items() if k != "accept_terms"}
    )
    assert r.status_code == 422
    assert count(engine_seeded, Customer) == before, (
        "nothing is created without consent"
    )


def test_welcome_email(client: TestClient, mailer: mail.Mailer, outbox: Path) -> None:
    assert client.post("/api/auth/signup", json=ZARA).status_code == 201
    [m] = inbox(mailer, outbox)
    assert m["To"] == "zara@example.com" and m["Subject"] == "Welcome to Kavrelle"
    assert "Hi Zara Malik" in body(m) and "demonstration store" in body(m)
    assert ZARA["password"] not in m.as_string()


# ---------- order emails ----------


def test_order_confirmation_then_cancellation_emails(
    client: TestClient, mailer: mail.Mailer, outbox: Path
) -> None:
    token = client.post("/api/cart").json()["token"]
    client.post(
        f"/api/cart/{token}/items",
        json={"product_slug": "street-sneaker", "variant": "42", "quantity": 2},
    )
    order = client.post(f"/api/cart/{token}/checkout",
                        json={"name": "Zara Malik", "email": "zara@example.com", "zip_code": "75500"}).json()  # fmt: skip
    [conf] = inbox(mailer, outbox)
    text = body(conf)
    assert (
        conf["Subject"] == f"Your Kavrelle order {order['code']}"
        and conf["To"] == "zara@example.com"
    )
    assert (
        f"Order code: {order['code']}" in text
        and "2 x Velocity Knit Runner (42)" in text
    )
    assert "Total: $278.00" in text and "Track order" in text
    client.post(
        f"/api/orders/{order['code']}/cancel",
        json={"email": "zara@example.com", "zip_code": "75500"},
    )
    cancel = inbox(mailer, outbox)[-1]
    assert cancel["Subject"] == f"Order {order['code']} is cancelled"


def test_refund_email_and_failed_actions_send_nothing(
    client: TestClient, mailer: mail.Mailer, outbox: Path
) -> None:
    assert (
        client.post(
            "/api/orders/A102/refund", json={**ALI, "amount_cents": 999999}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/orders/A102/cancel", json={**ALI, "zip_code": "00000"}
        ).status_code
        == 404
    )
    assert inbox(mailer, outbox) == [], "no email for a refused action"
    assert (
        client.post(
            "/api/orders/A102/refund", json={**ALI, "amount_cents": 4500}
        ).status_code
        == 200
    )
    [m] = inbox(mailer, outbox)
    assert m["Subject"] == "Refund for order A102" and "$45.00" in body(m)


# ---------- password reset ----------


def _reset_link(mailer: mail.Mailer, outbox: Path) -> str:
    m = inbox(mailer, outbox)[-1]
    assert m["Subject"] == "Reset your Kavrelle password"
    found = re.search(r"http://testserver/reset\?token=([\w-]+)", body(m))
    assert found, body(m)
    return found.group(1)


def test_forgot_password_does_not_reveal_accounts(
    client: TestClient, mailer: mail.Mailer, outbox: Path
) -> None:
    client.post("/api/auth/signup", json=ZARA)
    a = client.post("/api/auth/forgot", json={"email": "zara@example.com"})
    b = client.post("/api/auth/forgot", json={"email": "nobody@example.com"})
    c = client.post(
        "/api/auth/forgot", json={"email": "ali@example.com"}
    )  # guest, no password
    assert a.status_code == b.status_code == c.status_code == 202
    assert a.json() == b.json() == c.json()
    sent = [m["To"] for m in inbox(mailer, outbox) if m["Subject"].startswith("Reset")]
    assert sent == ["zara@example.com"]


def test_full_password_reset(
    client: TestClient, engine_seeded: Engine, mailer: mail.Mailer, outbox: Path
) -> None:
    client.post("/api/auth/signup", json=ZARA)  # signed in on this "device"
    other = TestClient(client.app)
    other.post(
        "/api/auth/login",
        json={"email": "zara@example.com", "password": ZARA["password"]},
    )
    client.post("/api/auth/forgot", json={"email": "Zara@Example.com "})
    token = _reset_link(mailer, outbox)
    with session_scope(make_session_factory(engine_seeded)) as s:
        assert token not in list(s.scalars(select(PasswordReset.token_hash))), (
            "only the hash is stored"
        )
    r = client.post("/api/auth/reset", json={"token": token, "password": "newsilk2027"})
    assert r.status_code == 204
    assert count(engine_seeded, AuthSession) == 0, "every device is signed out"
    assert other.get("/api/me").status_code == 401
    old = client.post(
        "/api/auth/login",
        json={"email": "zara@example.com", "password": ZARA["password"]},
    )
    assert old.status_code == 422
    new = client.post(
        "/api/auth/login", json={"email": "zara@example.com", "password": "newsilk2027"}
    )
    assert new.status_code == 200
    again = client.post(
        "/api/auth/reset", json={"token": token, "password": "another2028"}
    )
    assert again.status_code == 422 and "not valid" in again.json()["detail"], (
        "links work once"
    )


def test_reset_rejects_bad_tokens_weak_passwords_and_expired_links(
    client: TestClient, engine_seeded: Engine, mailer: mail.Mailer, outbox: Path
) -> None:
    client.post("/api/auth/signup", json=ZARA)
    assert (
        client.post(
            "/api/auth/reset", json={"token": "made-up", "password": "newsilk2027"}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/auth/reset", json={"token": "", "password": "newsilk2027"}
        ).status_code
        == 422
    )
    client.post("/api/auth/forgot", json={"email": "zara@example.com"})
    token = _reset_link(mailer, outbox)
    weak = client.post("/api/auth/reset", json={"token": token, "password": "short"})
    assert weak.status_code == 422
    with session_scope(make_session_factory(engine_seeded)) as s:
        s.execute(
            update(PasswordReset).values(
                expires_at=datetime.now(UTC) - timedelta(minutes=1)
            )
        )
    r = client.post("/api/auth/reset", json={"token": token, "password": "newsilk2027"})
    assert r.status_code == 422 and "expired" in r.json()["detail"]
    ok = client.post(
        "/api/auth/login",
        json={"email": "zara@example.com", "password": ZARA["password"]},
    )
    assert ok.status_code == 200, "a failed reset leaves the old password working"


def test_forgot_is_rate_limited_quietly(
    client: TestClient, mailer: mail.Mailer, outbox: Path
) -> None:
    client.post("/api/auth/signup", json=ZARA)
    for _ in range(6):
        assert (
            client.post(
                "/api/auth/forgot", json={"email": "zara@example.com"}
            ).status_code
            == 202
        )
    resets = [m for m in inbox(mailer, outbox) if m["Subject"].startswith("Reset")]
    assert len(resets) == 3, "at most 3 reset emails per 15 minutes"


# ---------- contact ----------


def test_contact_message_is_stored_and_acknowledged(
    client: TestClient, engine_seeded: Engine, mailer: mail.Mailer, outbox: Path
) -> None:
    r = client.post("/api/contact", json={**MSG, "email": " Hina@Example.com "})
    assert r.status_code == 202
    with session_scope(make_session_factory(engine_seeded)) as s:
        row = s.scalar(select(ContactMessage))
        assert (
            row is not None
            and row.email == "hina@example.com"
            and row.topic == "returns"
        )
    [m] = inbox(mailer, outbox)
    assert m["To"] == "hina@example.com" and m["Subject"] == "We got your message"


@pytest.mark.parametrize(
    "change",
    [{"name": " "}, {"email": "not-an-email"}, {"topic": "spam"}, {"message": "short"},
     {"message": "x" * 3001}, {"name": "x" * 81}],
)  # fmt: skip
def test_contact_validation(
    client: TestClient, engine_seeded: Engine, change: dict[str, str]
) -> None:
    r = client.post("/api/contact", json={**MSG, **change})
    assert r.status_code == 422 and isinstance(r.json()["detail"], (str, list))
    assert count(engine_seeded, ContactMessage) == 0


def test_contact_honeypot_and_rate_limit(
    client: TestClient, engine_seeded: Engine, mailer: mail.Mailer, outbox: Path
) -> None:
    r = client.post("/api/contact", json={**MSG, "website": "http://spam.example"})
    assert r.status_code == 202 and count(engine_seeded, ContactMessage) == 0
    assert inbox(mailer, outbox) == []
    for _ in range(5):
        assert client.post("/api/contact", json=MSG).status_code == 202
    r = client.post("/api/contact", json=MSG)
    assert r.status_code == 429 and "try again later" in r.json()["detail"]
    assert count(engine_seeded, ContactMessage) == 5


def test_admin_contact_inbox(client: TestClient, engine_seeded: Engine) -> None:
    client.post("/api/contact", json=MSG)
    assert client.get("/api/admin/messages").status_code == 401
    client.post("/api/auth/signup", json=ZARA)
    assert client.get("/api/admin/messages").status_code == 403
    with session_scope(make_session_factory(engine_seeded)) as s:
        s.execute(
            update(Customer)
            .where(Customer.email == "zara@example.com")
            .values(is_admin=True)
        )
    [m] = client.get("/api/admin/messages").json()
    assert (
        m["name"] == "Hina"
        and m["message"] == MSG["message"]
        and m["topic"] == "returns"
    )


# ---------- the mailer itself ----------


def test_mailer_blocks_header_injection(tmp_path: Path) -> None:
    m = mail.Mailer(outbox=tmp_path)
    m.send(
        mail.Email(
            "a@example.com\r\nBcc: victim@example.com", "Hi\nBcc: x@example.com", "body"
        )
    )
    m.flush()
    [f] = list(tmp_path.glob("*.eml"))
    msg = message_from_bytes(f.read_bytes(), policy=policy.default)
    assert msg["Bcc"] is None and "\n" not in str(msg["Subject"])


def test_mailer_failure_never_breaks_the_shop(
    monkeypatch: pytest.MonkeyPatch, caplog: pytest.LogCaptureFixture
) -> None:
    with socket.socket() as s:  # a port with nothing listening
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    monkeypatch.setenv("KAVRELLE_SMTP_HOST", "127.0.0.1")
    monkeypatch.setenv("KAVRELLE_SMTP_PORT", str(port))
    m = mail.Mailer()
    m.send(mail.welcome("Zara", "zara@example.com")).result(
        timeout=10
    )  # returns, does not raise
    assert "could not send" in caplog.text
    m.shutdown()


def test_mailer_uses_smtp_when_configured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    sent: list[EmailMessage] = []

    class FakeSMTP:
        def __init__(self, host: str, port: int, timeout: float) -> None:
            assert (host, port) == ("smtp.example.com", 587)

        def __enter__(self) -> Self:
            return self

        def __exit__(self, *a: object) -> None:
            return None

        def starttls(self, context: object) -> None:
            sent.append(EmailMessage())  # marker: TLS was started first
            sent.pop()

        def login(self, user: str, password: str) -> None:
            assert (user, password) == ("shop", "secret")

        def send_message(self, msg: EmailMessage) -> None:
            sent.append(msg)

    monkeypatch.setattr(smtplib, "SMTP", FakeSMTP)
    for k, v in {"KAVRELLE_SMTP_HOST": "smtp.example.com", "KAVRELLE_SMTP_USER": "shop",
                 "KAVRELLE_SMTP_PASSWORD": "secret", "KAVRELLE_MAIL_FROM": "Kavrelle <hello@example.com>"}.items():  # fmt: skip
        monkeypatch.setenv(k, v)
    m = mail.Mailer(outbox=tmp_path)
    m.send(mail.contact_receipt("Hina", "hina@example.com", "order")).result(timeout=10)
    assert len(sent) == 1 and sent[0]["From"] == "Kavrelle <hello@example.com>"
    assert list(tmp_path.iterdir()) == [], "SMTP mode writes nothing to disk"
