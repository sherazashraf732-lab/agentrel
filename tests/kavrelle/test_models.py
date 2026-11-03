"""The database itself must refuse bad data, not only the Python code."""

from collections.abc import Callable

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from kavrelle.models import (
    METRIC_KEYS,
    Customer,
    EvalRun,
    MetricResult,
    Order,
    Product,
    Shop,
    Variant,
    Violation,
)


def _shop(slug: str = "s1", position: int = 1) -> Shop:
    return Shop(slug=slug, name="Shop", category="Fashion", floor=1, position=position)


def _customer(email: str = "a@example.com") -> Customer:
    return Customer(name="A", email=email, zip_code="54000")


def _rejected(session: Session, *objs: object) -> None:
    session.add_all(objs)
    with pytest.raises(IntegrityError):
        session.flush()
    session.rollback()


def test_metric_keys_are_the_papers_twelve() -> None:
    assert len(METRIC_KEYS) == 12
    assert len(set(METRIC_KEYS)) == 12


@pytest.mark.parametrize(
    "build",
    [
        lambda: [Product(shop=_shop(), slug="p", name="P", price_cents=-1)],
        lambda: [Variant(product=Product(shop=_shop(), slug="p", name="P", price_cents=1),
                         label="M", stock=-5)],
        lambda: [Order(code="X1", customer=_customer(), status="lost", total_cents=10)],
        lambda: [Order(code="X1", customer=_customer(), total_cents=10, refunded_cents=11)],
        lambda: [Order(code="X1", customer=_customer(), total_cents=-1)],
        lambda: [_shop("dup", 1), _shop("dup", 2)],
        lambda: [_shop("a", 1), _shop("b", 1)],
        lambda: [_customer("same@example.com"), _customer("same@example.com")],
        lambda: [MetricResult(run=EvalRun(name="r", assistant_label="v1"), key="C_out", value=1.5)],
        lambda: [EvalRun(name="r", assistant_label="v1", status="maybe")],
        lambda: [EvalRun(name="r", assistant_label="v1", n_runs=0)],
        lambda: [Violation(run=EvalRun(name="r", assistant_label="v1"), task_id="t", rule="x",
                           severity="extreme")],
    ],
    ids=["negative-price", "negative-stock", "bad-status", "refund-over-total", "negative-total",
         "duplicate-slug", "same-shop-position", "duplicate-email", "metric-above-1",
         "bad-run-status", "zero-runs", "bad-severity"],
)  # fmt: skip
def test_database_rejects_invalid_rows(
    session: Session, build: Callable[[], list[object]]
) -> None:
    _rejected(session, *build())


def test_deleting_a_shop_removes_its_products_and_variants(session: Session) -> None:
    shop = _shop()
    shop.products.append(Product(slug="p", name="P", price_cents=100,
                                 variants=[Variant(label="M", stock=1)]))  # fmt: skip
    session.add(shop)
    session.commit()
    session.delete(shop)
    session.commit()
    assert session.scalar(select(func.count(Product.id))) == 0
    assert session.scalar(select(func.count(Variant.id))) == 0


def test_customer_with_orders_cannot_be_deleted(session: Session) -> None:
    customer = _customer()
    session.add(Order(code="A1", customer=customer, total_cents=100))
    session.commit()
    session.expunge_all()
    with pytest.raises(IntegrityError):
        session.execute(
            Customer.__table__.delete().where(Customer.email == "a@example.com")
        )
        session.flush()
    session.rollback()


def test_metric_value_may_be_missing(session: Session) -> None:
    run = EvalRun(name="r", assistant_label="v1")
    run.metrics.append(MetricResult(key="P_auroc", value=None))
    session.add(run)
    session.commit()
    assert session.scalar(select(MetricResult.value)) is None
