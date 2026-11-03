"""The shopping API end to end: status codes, validation and privacy."""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from kavrelle.api import create_app
from kavrelle.config import Settings
from kavrelle.db import make_session_factory, session_scope
from kavrelle.seed import seed

ZARA = {"name": "Zara Malik", "email": "zara@example.com", "zip_code": "75500"}


@pytest.fixture
def client(engine: Engine) -> TestClient:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    return TestClient(create_app(Settings(), engine=engine))


@pytest.fixture
def token(client: TestClient) -> str:
    r = client.post("/api/cart")
    assert r.status_code == 201
    return str(r.json()["token"])


def test_full_shopping_journey(client: TestClient, token: str) -> None:
    r = client.post(
        f"/api/cart/{token}/items",
        json={"product_slug": "tech-fleece-hoodie", "variant": "L"},
    )
    assert r.status_code == 200 and r.json()["item_count"] == 1
    r = client.post(
        f"/api/cart/{token}/items",
        json={"product_slug": "air-runner-2", "variant": "42", "quantity": 2},
    )
    cart = r.json()
    assert cart["total_cents"] == 9897 + 2 * 12900 and cart["item_count"] == 3
    shoe = next(i for i in cart["items"] if i["product_slug"] == "air-runner-2")
    r = client.patch(f"/api/cart/{token}/items/{shoe['id']}", json={"quantity": 1})
    assert r.json()["total_cents"] == 9897 + 12900
    r = client.post(f"/api/cart/{token}/checkout", json=ZARA)
    assert r.status_code == 201
    order = r.json()
    assert order["status"] == "pending" and order["total_cents"] == 9897 + 12900
    assert client.get(f"/api/cart/{token}").json()["items"] == []
    r = client.post(
        f"/api/orders/{order['code']}/lookup",
        json={"email": ZARA["email"], "zip_code": "75500"},
    )
    assert r.status_code == 200 and len(r.json()["items"]) == 2
    r = client.post(
        f"/api/orders/{order['code']}/cancel",
        json={"email": ZARA["email"], "zip_code": "75500"},
    )
    assert r.json()["status"] == "cancelled"


def test_remove_line(client: TestClient, token: str) -> None:
    item = client.post(
        f"/api/cart/{token}/items", json={"product_slug": "mouse-pad", "variant": "M"}
    ).json()["items"][0]
    assert client.delete(f"/api/cart/{token}/items/{item['id']}").json()["items"] == []


@pytest.mark.parametrize(
    ("body", "status"),
    [
        ({"product_slug": "mouse-pad", "variant": "M", "quantity": 0}, 422),
        ({"product_slug": "mouse-pad", "variant": "M", "quantity": 11}, 422),
        ({"product_slug": "mouse-pad", "variant": "M", "quantity": "lots"}, 422),
        ({"product_slug": "mouse-pad"}, 422),
        ({"product_slug": "nope", "variant": "M"}, 404),
        ({"product_slug": "mouse-pad", "variant": "XL"}, 404),
    ],
)
def test_add_item_validation(
    client: TestClient, token: str, body: dict[str, object], status: int
) -> None:
    assert client.post(f"/api/cart/{token}/items", json=body).status_code == status


def test_unknown_cart_is_404(client: TestClient) -> None:
    assert client.get("/api/cart/nope").status_code == 404
    assert client.post("/api/cart/nope/checkout", json=ZARA).status_code == 404


def test_empty_cart_checkout_is_422(client: TestClient, token: str) -> None:
    r = client.post(f"/api/cart/{token}/checkout", json=ZARA)
    assert r.status_code == 422 and r.json()["detail"] == "Your cart is empty"


def test_policy_violations_get_clear_errors(client: TestClient) -> None:
    ali = {"email": "ali@example.com", "zip_code": "54000"}
    assert client.post("/api/orders/A100/cancel", json=ali).status_code == 409
    assert (
        client.post(
            "/api/orders/A101/refund", json={**ali, "amount_cents": 100}
        ).status_code
        == 409
    )
    assert (
        client.post(
            "/api/orders/A102/refund", json={**ali, "amount_cents": 999999}
        ).status_code
        == 422
    )
    assert (
        client.post(
            "/api/orders/A102/refund", json={**ali, "amount_cents": 4500}
        ).json()["status"]
        == "refunded"
    )


def test_wrong_identity_looks_like_missing_order(client: TestClient) -> None:
    """Never reveal that an order exists to someone who is not its owner."""
    other = client.post(
        "/api/orders/A104/lookup",
        json={"email": "ali@example.com", "zip_code": "54000"},
    )
    missing = client.post(
        "/api/orders/ZZZZ/lookup",
        json={"email": "ali@example.com", "zip_code": "54000"},
    )
    assert other.status_code == missing.status_code == 404
    assert other.json() == missing.json()


def test_identity_never_goes_in_the_url(client: TestClient) -> None:
    for path, item in client.app.openapi()["paths"].items():  # type: ignore[attr-defined]
        for op in item.values():
            names = {p["name"] for p in op.get("parameters", [])}
            assert not names & {"email", "zip_code"}, (
                f"{path} puts personal data in the URL"
            )


def test_errors_never_leak_internals(client: TestClient, token: str) -> None:
    r = client.post(
        f"/api/cart/{token}/items",
        json={"product_slug": "x' OR 1=1 --", "variant": "M"},
    )
    assert r.status_code == 404
    text = r.text.lower()
    assert "traceback" not in text and "sqlalchemy" not in text and "select" not in text
