"""Cart, checkout and order-policy rules, tested directly on the database."""

from collections.abc import Iterator

import pytest
from sqlalchemy import Engine, select
from sqlalchemy.orm import Session

from kavrelle import shop
from kavrelle.db import make_session_factory
from kavrelle.models import Order, Product, Variant
from kavrelle.seed import seed

ALI = ("ali@example.com", "54000")
SARA = ("sara@example.com", "44000")
BUYER = shop.Buyer("Zara Malik", "Zara@Example.com ", "75500")


@pytest.fixture
def db(engine: Engine) -> Iterator[Session]:
    with make_session_factory(engine)() as s:
        seed(s)
        s.commit()
        yield s


def _stock(db: Session, slug: str, label: str) -> int:
    return (
        db.scalar(
            select(Variant.stock)
            .join(Product)
            .where(Product.slug == slug, Variant.label == label)
        )
        or 0
    )


def _set_stock(db: Session, slug: str, label: str, n: int) -> None:
    v = db.scalar(
        select(Variant)
        .join(Product)
        .where(Product.slug == slug, Variant.label == label)
    )
    assert v is not None
    v.stock = n
    db.commit()


# ---------- cart ----------


def test_same_item_merges_into_one_line(db: Session) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "floral-print-shirt", "L", 1)
    cart = shop.add_item(db, token, "floral-print-shirt", "L", 2)
    assert len(cart.items) == 1 and cart.items[0].quantity == 3
    assert shop.cart_total_cents(cart) == 3 * 5900


@pytest.mark.parametrize(
    ("slug", "variant", "qty", "error"),
    [
        ("no-such-product", "L", 1, shop.NotFound),
        ("floral-print-shirt", "XXXL", 1, shop.NotFound),
        ("floral-print-shirt", "L", 0, shop.Invalid),
        ("floral-print-shirt", "L", 11, shop.Invalid),
        ("floral-print-shirt", "L", -3, shop.Invalid),
    ],
)
def test_bad_additions_are_refused(
    db: Session, slug: str, variant: str, qty: int, error: type
) -> None:
    token = shop.create_cart(db).token
    with pytest.raises(error):
        shop.add_item(db, token, slug, variant, qty)


def test_cannot_add_more_than_stock(db: Session) -> None:
    _set_stock(db, "street-sneaker", "42", 2)
    token = shop.create_cart(db).token
    shop.add_item(db, token, "street-sneaker", "42", 2)
    with pytest.raises(shop.Conflict, match="Only 2 left"):
        shop.add_item(db, token, "street-sneaker", "42", 1)


def test_merged_line_cannot_pass_the_limit(db: Session) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "mouse-pad", "M", 9)
    with pytest.raises(shop.Invalid):
        shop.add_item(db, token, "mouse-pad", "M", 2)


def test_inactive_products_cannot_be_bought(db: Session) -> None:
    p = db.scalar(select(Product).where(Product.slug == "monitor-27"))
    assert p is not None
    p.active = False
    db.commit()
    with pytest.raises(shop.NotFound):
        shop.add_item(db, shop.create_cart(db).token, "monitor-27", "Fixed", 1)


def test_set_quantity_and_remove(db: Session) -> None:
    token = shop.create_cart(db).token
    cart = shop.add_item(db, token, "mouse-pad", "M", 1)
    line_id = cart.items[0].id
    assert shop.set_quantity(db, token, line_id, 4).items[0].quantity == 4
    assert shop.set_quantity(db, token, line_id, 0).items == []
    with pytest.raises(shop.NotFound):
        shop.set_quantity(db, token, line_id, 1)


def test_unknown_cart(db: Session) -> None:
    with pytest.raises(shop.NotFound):
        shop.get_cart(db, "not-a-real-token")


# ---------- checkout ----------


def test_checkout_creates_order_takes_stock_and_empties_cart(db: Session) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "floral-print-shirt", "L", 2)
    shop.add_item(db, token, "mouse-pad", "L", 1)
    before = _stock(db, "floral-print-shirt", "L")
    order = shop.checkout(db, token, BUYER)
    db.commit()
    assert order.status == "pending"
    assert order.total_cents == 2 * 5900 + 1000
    assert order.code.startswith("K") and len(order.code) == 8
    assert order.customer.email == "zara@example.com"
    assert _stock(db, "floral-print-shirt", "L") == before - 2
    assert shop.get_cart(db, token).items == []


def test_price_is_frozen_at_checkout(db: Session) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "mouse-pad", "M", 1)
    order = shop.checkout(db, token, BUYER)
    db.commit()
    p = db.scalar(select(Product).where(Product.slug == "mouse-pad"))
    assert p is not None
    p.price_cents = 99999
    db.commit()
    db.refresh(order)
    assert order.total_cents == 1000 and order.items[0].unit_price_cents == 1000


def test_empty_cart_cannot_check_out(db: Session) -> None:
    with pytest.raises(shop.Invalid, match="empty"):
        shop.checkout(db, shop.create_cart(db).token, BUYER)


@pytest.mark.parametrize(
    "buyer",
    [
        shop.Buyer("", "a@b.co", "54000"),
        shop.Buyer("A", "not-an-email", "54000"),
        shop.Buyer("A", "a@b.co", "!!"),
        shop.Buyer("A" * 81, "a@b.co", "54000"),
    ],
)
def test_bad_buyer_details_are_refused(db: Session, buyer: shop.Buyer) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "mouse-pad", "M", 1)
    with pytest.raises(shop.Invalid):
        shop.checkout(db, token, buyer)


def test_cannot_check_out_as_someone_else(db: Session) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "mouse-pad", "M", 1)
    with pytest.raises(shop.Conflict):
        shop.checkout(db, token, shop.Buyer("Fake Ali", "ali@example.com", "99999"))


def test_two_shoppers_cannot_oversell_the_last_items(db: Session) -> None:
    _set_stock(db, "clean-low-top", "44", 3)
    first, second = shop.create_cart(db).token, shop.create_cart(db).token
    shop.add_item(db, first, "clean-low-top", "44", 2)
    shop.add_item(db, second, "clean-low-top", "44", 2)
    db.commit()
    shop.checkout(db, first, BUYER)
    db.commit()
    with pytest.raises(shop.Conflict, match="sold out"):
        shop.checkout(db, second, shop.Buyer("Omar", "omar@example.com", "54000"))
    db.rollback()
    assert _stock(db, "clean-low-top", "44") == 1
    assert len(shop.get_cart(db, second).items) == 1, (
        "failed checkout must leave the cart intact"
    )


def test_failed_checkout_takes_no_stock_at_all(db: Session) -> None:
    token = shop.create_cart(db).token
    shop.add_item(db, token, "mouse-pad", "M", 2)  # this line is taken first...
    shop.add_item(db, token, "phone-case", "Pro", 1)
    _set_stock(db, "phone-case", "Pro", 0)  # ...then this one sells out before checkout
    before = _stock(db, "mouse-pad", "M")
    with pytest.raises(shop.Conflict):
        shop.checkout(db, token, BUYER)
    db.rollback()
    assert _stock(db, "mouse-pad", "M") == before
    assert (
        db.scalar(select(Order).where(Order.customer.has(email="zara@example.com")))
        is None
    )


# ---------- order policy (the assistant's four rules) ----------


def test_lookup_needs_matching_identity(db: Session) -> None:
    assert shop.find_order(db, "a101", " ALI@example.com ", "54000").code == "A101"
    for email, zip_code in [
        SARA,
        ("ali@example.com", "00000"),
        ("x@example.com", "54000"),
    ]:
        with pytest.raises(shop.NotFound):
            shop.find_order(db, "A101", email, zip_code)


def test_cancel_pending_returns_stock(db: Session) -> None:
    before = _stock(db, "wireless-headphones", "Black")
    order = shop.cancel_order(db, "A101", *ALI)
    db.commit()
    assert order.status == "cancelled"
    assert _stock(db, "wireless-headphones", "Black") == before + 1


@pytest.mark.parametrize("code", ["A100", "A102"])
def test_only_pending_orders_can_be_cancelled(db: Session, code: str) -> None:
    with pytest.raises(shop.Conflict, match="Only pending"):
        shop.cancel_order(db, code, *ALI)


def test_cannot_cancel_twice(db: Session) -> None:
    shop.cancel_order(db, "A101", *ALI)
    db.commit()
    with pytest.raises(shop.Conflict):
        shop.cancel_order(db, "A101", *ALI)


def test_cannot_touch_another_customers_order(db: Session) -> None:
    with pytest.raises(shop.NotFound):
        shop.cancel_order(db, "A104", *ALI)
    with pytest.raises(shop.NotFound):
        shop.refund_order(db, "A103", *ALI, 1000)


def test_partial_then_full_refund(db: Session) -> None:
    order = shop.refund_order(db, "A103", *SARA, 1000)
    assert (order.status, order.refunded_cents) == ("delivered", 1000)
    order = shop.refund_order(db, "A103", *SARA, 2000)
    assert (order.status, order.refunded_cents) == ("refunded", 3000)


@pytest.mark.parametrize("amount", [0, -100, 3001])
def test_refund_amount_must_be_in_range(db: Session, amount: int) -> None:
    with pytest.raises(shop.Invalid):
        shop.refund_order(db, "A103", *SARA, amount)


@pytest.mark.parametrize("code", ["A100", "A101"])
def test_only_delivered_orders_can_be_refunded(db: Session, code: str) -> None:
    with pytest.raises(shop.Conflict, match="Only delivered"):
        shop.refund_order(db, code, *ALI, 100)
