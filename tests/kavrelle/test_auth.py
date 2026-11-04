"""Accounts: sign up, sign in, sessions, privacy and brute-force protection."""

from datetime import UTC, datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update

from kavrelle.api import create_app
from kavrelle.auth import hash_password, verify_password
from kavrelle.config import Settings
from kavrelle.db import make_session_factory, session_scope
from kavrelle.models import AuthSession, Customer
from kavrelle.seed import seed

ZARA = {
    "name": "Zara Malik",
    "email": "Zara@Example.com",
    "zip_code": "75500",
    "password": "silk2026scarf",
}


@pytest.fixture
def engine_seeded(engine: Engine) -> Engine:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    return engine


@pytest.fixture
def client(engine_seeded: Engine) -> TestClient:
    return TestClient(create_app(Settings(), engine=engine_seeded))


def test_signup_signs_you_in(client: TestClient) -> None:
    r = client.post("/api/auth/signup", json=ZARA)
    assert r.status_code == 201
    assert r.json() == {
        "name": "Zara Malik",
        "email": "zara@example.com",
        "zip_code": "75500",
    }
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "samesite=lax" in cookie
    assert client.get("/api/me").json()["email"] == "zara@example.com"


def test_logout_ends_the_session(client: TestClient) -> None:
    client.post("/api/auth/signup", json=ZARA)
    assert client.post("/api/auth/logout").status_code == 204
    assert client.get("/api/me").status_code == 401


def test_sign_in_again(client: TestClient) -> None:
    client.post("/api/auth/signup", json=ZARA)
    client.post("/api/auth/logout")
    r = client.post(
        "/api/auth/login",
        json={"email": " zara@example.com ", "password": ZARA["password"]},
    )
    assert r.status_code == 200 and client.get("/api/me").status_code == 200


def test_duplicate_account_is_refused(client: TestClient) -> None:
    client.post("/api/auth/signup", json=ZARA)
    assert client.post("/api/auth/signup", json=ZARA).status_code == 409


def test_existing_customer_can_claim_account_only_with_matching_zip(
    client: TestClient,
) -> None:
    wrong = {
        "name": "Ali",
        "email": "ali@example.com",
        "zip_code": "99999",
        "password": "orders2026x",
    }
    assert client.post("/api/auth/signup", json=wrong).status_code == 409
    right = {**wrong, "zip_code": "54000"}
    assert client.post("/api/auth/signup", json=right).status_code == 201
    codes = [o["code"] for o in client.get("/api/me/orders").json()]
    assert set(codes) == {"A100", "A101", "A102"}


@pytest.mark.parametrize(
    "password", ["short1", "allletterspassword", "1234567890", "x" * 129]
)
def test_weak_passwords_are_refused(client: TestClient, password: str) -> None:
    assert (
        client.post("/api/auth/signup", json={**ZARA, "password": password}).status_code
        == 422
    )


def test_wrong_password_and_unknown_email_look_the_same(client: TestClient) -> None:
    client.post("/api/auth/signup", json=ZARA)
    client.post("/api/auth/logout")
    a = client.post(
        "/api/auth/login", json={"email": "zara@example.com", "password": "wrongpass99"}
    )
    b = client.post(
        "/api/auth/login",
        json={"email": "nobody@example.com", "password": "wrongpass99"},
    )
    assert a.status_code == b.status_code == 422 and a.json() == b.json()


def test_guest_customer_without_password_cannot_sign_in(client: TestClient) -> None:
    r = client.post(
        "/api/auth/login", json={"email": "ali@example.com", "password": "anything123"}
    )
    assert r.status_code == 422


def test_brute_force_is_rate_limited(client: TestClient) -> None:
    client.post("/api/auth/signup", json=ZARA)
    client.post("/api/auth/logout")
    for _ in range(5):
        client.post(
            "/api/auth/login",
            json={"email": "zara@example.com", "password": "guess12345"},
        )
    r = client.post(
        "/api/auth/login",
        json={"email": "zara@example.com", "password": ZARA["password"]},
    )
    assert r.status_code == 429


def test_passwords_and_tokens_are_never_stored_in_clear(
    client: TestClient, engine_seeded: Engine
) -> None:
    r = client.post("/api/auth/signup", json=ZARA)
    token = r.cookies.get("kv_session") or client.cookies.get("kv_session")
    with session_scope(make_session_factory(engine_seeded)) as s:
        c = s.scalar(select(Customer).where(Customer.email == "zara@example.com"))
        assert (
            c is not None
            and c.password_hash
            and ZARA["password"] not in c.password_hash
        )
        assert c.password_hash.startswith("scrypt$")
        hashes = list(s.scalars(select(AuthSession.token_hash)))
        assert token and token not in hashes and len(hashes[0]) == 64


def test_password_hashing() -> None:
    h = hash_password("silk2026scarf")
    assert verify_password("silk2026scarf", h)
    assert not verify_password("silk2026scarF", h)
    assert not verify_password("x", "garbage")
    assert hash_password("same1234pw") != hash_password("same1234pw"), "salted"


def test_expired_session_is_rejected(client: TestClient, engine_seeded: Engine) -> None:
    client.post("/api/auth/signup", json=ZARA)
    with session_scope(make_session_factory(engine_seeded)) as s:
        s.execute(
            update(AuthSession).values(
                expires_at=datetime.now(UTC) - timedelta(minutes=1)
            )
        )
    assert client.get("/api/me").status_code == 401


def test_my_orders_needs_sign_in_and_shows_only_mine(client: TestClient) -> None:
    assert client.get("/api/me/orders").status_code == 401
    client.post("/api/auth/signup", json={"name": "Sara", "email": "sara@example.com", "zip_code": "44000",
                                          "password": "monitor2026"})  # fmt: skip
    codes = {o["code"] for o in client.get("/api/me/orders").json()}
    assert codes == {"A103", "A104"}


def test_forged_cookie_is_ignored(client: TestClient) -> None:
    client.cookies.set("kv_session", "forged-token")
    assert client.get("/api/me").status_code == 401
    client.cookies.set("kv_session", "x" * 5000)
    assert client.get("/api/me").status_code == 401
