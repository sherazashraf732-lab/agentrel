import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from agentrel.envs.store import CUSTOMERS as STORE_CUSTOMERS
from agentrel.envs.store import ORDERS as STORE_ORDERS
from kavrelle import catalog
from kavrelle.models import Order, Product, Shop, Variant
from kavrelle.seed import seed


def _count(session: Session, model: type) -> int:
    return session.scalar(select(func.count()).select_from(model)) or 0


def test_seed_loads_whole_catalogue(session: Session) -> None:
    result = seed(session)
    session.commit()
    n_products = sum(len(s["products"]) for s in catalog.SHOPS)
    assert result.shops == _count(session, Shop) == len(catalog.SHOPS) == 12
    assert result.products == _count(session, Product) == n_products
    assert _count(session, Variant) == sum(len(p["variants"]) for s in catalog.SHOPS
                                           for p in s["products"])  # fmt: skip
    assert result.customers == len(catalog.CUSTOMERS)
    assert result.orders == len(catalog.ORDERS)


def test_seed_twice_adds_nothing(session: Session) -> None:
    seed(session)
    session.commit()
    again = seed(session)
    session.commit()
    assert (again.shops, again.products, again.customers, again.orders) == (0, 0, 0, 0)
    assert _count(session, Shop) == 12


def test_seed_never_overwrites_a_changed_order(session: Session) -> None:
    seed(session)
    session.commit()
    order = session.scalar(select(Order).where(Order.code == "A101"))
    assert order is not None
    order.status = "cancelled"
    session.commit()
    seed(session)
    session.commit()
    assert (
        session.scalar(select(Order.status).where(Order.code == "A101")) == "cancelled"
    )


@pytest.mark.parametrize("code", sorted(STORE_ORDERS))
def test_orders_match_the_agent_store(session: Session, code: str) -> None:
    """The website and the agent's test store must describe the same orders."""
    seed(session)
    session.commit()
    order = session.scalar(select(Order).where(Order.code == code))
    expected = STORE_ORDERS[code]
    assert order is not None
    assert order.status == expected["status"]
    assert order.total_cents == round(expected["total"] * 100)
    assert order.customer.email == STORE_CUSTOMERS[expected["customer_id"]]["email"]
    assert order.customer.zip_code == STORE_CUSTOMERS[expected["customer_id"]]["zip"]
    assert len(order.items) == len(expected["items"])


def test_catalogue_is_well_formed() -> None:
    slugs = [p["slug"] for s in catalog.SHOPS for p in s["products"]]
    assert len(slugs) == len(set(slugs)), "product slugs must be unique"
    places = [(s["floor"], s["position"]) for s in catalog.SHOPS]
    assert len(places) == len(set(places)), "two shops in the same spot"
    for s in catalog.SHOPS:
        for p in s["products"]:
            assert p["price_cents"] > 0 and p["variants"], p["slug"]
    assert {c["email"] for c in catalog.CUSTOMERS} == {
        e for _, e, _, _ in catalog.ORDERS
    }
