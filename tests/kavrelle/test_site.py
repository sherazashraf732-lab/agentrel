"""The website files: served correctly, secure headers, real photos, no leaks."""

import re
from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine

from kavrelle import catalog
from kavrelle.api import WEB_DIR, create_app
from kavrelle.config import Settings
from kavrelle.db import make_session_factory, session_scope
from kavrelle.seed import seed

VENDOR_WORDS = ["ollama", "qwen", "llama", "openai", "litellm", "anthropic", "claude"]


@pytest.fixture
def client(engine: Engine) -> TestClient:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    return TestClient(create_app(Settings(), engine=engine))


def test_home_page_and_its_assets_load(client: TestClient) -> None:
    r = client.get("/")
    assert r.status_code == 200 and "text/html" in r.headers["content-type"]
    assets = re.findall(r'(?:src|href)="(/static/[^"]+)"', r.text)
    assert len(assets) >= 5
    for path in assets:
        assert client.get(path).status_code == 200, path


def test_security_headers(client: TestClient) -> None:
    for path in ["/", "/api/catalog", "/static/js/app.js"]:
        h = client.get(path).headers
        assert h["x-content-type-options"] == "nosniff"
        assert h["x-frame-options"] == "DENY"
        assert "default-src 'self'" in h["content-security-policy"]
        assert "'unsafe-inline'" not in h["content-security-policy"]
    assert client.get("/api/catalog").headers["cache-control"] == "no-store"


def test_page_obeys_its_own_security_policy() -> None:
    html = (WEB_DIR / "index.html").read_text("utf-8")
    assert 'style="' not in html, "inline style attributes are blocked by the CSP"
    assert not re.search(r"<script(?![^>]*\bsrc=)[^>]*>", html), (
        "inline scripts are blocked by the CSP"
    )
    assert not re.search(r"\son[a-z]+=", html), (
        "inline event handlers are blocked by the CSP"
    )


@pytest.mark.parametrize("name", ["app.js", "api.js", "mall3d.js"])
def test_scripts_never_inject_html(name: str) -> None:
    js = (WEB_DIR / "js" / name).read_text("utf-8")
    assert (
        "innerHTML" not in js
        and "outerHTML" not in js
        and "insertAdjacentHTML" not in js
    )
    assert "eval(" not in js and "new Function" not in js


def test_no_model_vendor_names_anywhere_in_the_site() -> None:
    for f in [
        WEB_DIR / "index.html",
        WEB_DIR / "styles.css",
        *sorted((WEB_DIR / "js").glob("*.js")),
    ]:
        text = f.read_text("utf-8").lower()
        for word in VENDOR_WORDS:
            assert word not in text, f"{word!r} found in {f.name}"


def test_catalog_endpoint(client: TestClient) -> None:
    shops = client.get("/api/catalog").json()
    assert len(shops) == 12
    assert [(s["floor"], s["position"]) for s in shops] == sorted(
        (s["floor"], s["position"]) for s in shops
    )
    elle = next(s for s in shops if s["slug"] == "maison-elle")
    assert len(elle["products"]) == 10
    assert all(p["description"] for p in elle["products"])


def test_every_product_has_a_real_licensed_photo() -> None:
    for shop in catalog.SHOPS:
        for p in shop["products"]:
            assert p["image_url"] == f"/static/img/products/{p['slug']}.jpg"
            assert p["image_credit"] and "pexels.com/photo/" in p["image_credit"]
            f = WEB_DIR / "img" / "products" / f"{p['slug']}.jpg"
            data = f.read_bytes()
            assert data[:3] == b"\xff\xd8\xff", f"{f.name} is not a JPEG"
            assert 10_000 < len(data) < 600_000, f"{f.name} size {len(data)}"


def test_photo_files_have_no_strays() -> None:
    slugs = {p["slug"] for s in catalog.SHOPS for p in s["products"]}
    files = {f.stem for f in Path(WEB_DIR / "img" / "products").glob("*.jpg")}
    assert files == slugs


def test_photos_are_served_with_the_right_type(client: TestClient) -> None:
    r = client.get("/static/img/products/black-evening-dress.jpg")
    assert r.status_code == 200 and r.headers["content-type"] == "image/jpeg"


def test_missing_static_file_is_404(client: TestClient) -> None:
    assert client.get("/static/nope.js").status_code == 404
    assert client.get("/static/../api.py").status_code == 404
