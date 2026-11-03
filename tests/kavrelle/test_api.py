from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from kavrelle import catalog
from kavrelle.api import create_app
from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, session_scope
from kavrelle.seed import seed


@pytest.fixture
def client(engine: Engine) -> TestClient:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    return TestClient(create_app(Settings(), engine=engine))


def test_health_ok(client: TestClient) -> None:
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "database": "ok", "brand": "Kavrelle"}


def test_health_reports_down_database_without_crashing(tmp_path: Path) -> None:
    missing_dir = tmp_path / "no" / "such" / "dir" / "db.sqlite"
    app = create_app(
        Settings(), engine=make_engine(f"sqlite:///{missing_dir.as_posix()}")
    )
    r = TestClient(app).get("/api/health")
    assert r.status_code == 503
    assert r.json()["database"] == "down"


def test_shops_listed_in_mall_order(client: TestClient) -> None:
    shops = client.get("/api/shops").json()
    assert [s["slug"] for s in shops] == [s["slug"] for s in sorted(
        catalog.SHOPS, key=lambda s: (s["floor"], s["position"]))]  # fmt: skip
    northline = next(s for s in shops if s["slug"] == "northline")
    assert northline["product_count"] == 8
    assert next(s for s in shops if s["slug"] == "food-court")["product_count"] == 0


def test_shop_detail_has_products_and_variants(client: TestClient) -> None:
    body = client.get("/api/shops/northline").json()
    assert body["name"] == "Northline"
    shirt = next(p for p in body["products"] if p["slug"] == "floral-print-shirt")
    assert shirt["price_cents"] == 5900
    assert [v["label"] for v in shirt["variants"]] == catalog.SIZES
    assert all(v["in_stock"] for v in shirt["variants"])


def test_product_detail(client: TestClient) -> None:
    r = client.get("/api/products/monitor-27")
    assert r.status_code == 200
    assert r.json()["price_cents"] == 15000


@pytest.mark.parametrize("path", ["/api/shops/nope", "/api/products/nope",
                                  "/api/shops/../../etc/passwd", "/api/shops/%27%20OR%201=1--"])  # fmt: skip
def test_unknown_or_hostile_slugs_are_404(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 404


def test_no_model_vendor_names_leak(client: TestClient) -> None:
    """The site must never reveal which model runs behind it."""
    text = " ".join(client.get(p).text for p in
                    ["/api/health", "/api/shops", "/api/shops/volt-and-co"]).lower()  # fmt: skip
    for word in ["ollama", "qwen", "llama", "openai", "litellm"]:
        assert word not in text
