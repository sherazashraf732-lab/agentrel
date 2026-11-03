"""Load the catalogue into the database. Safe to run many times (idempotent)."""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from kavrelle import catalog
from kavrelle.models import Customer, Order, OrderItem, Product, Shop, Variant


@dataclass(frozen=True)
class SeedResult:
    shops: int
    products: int
    customers: int
    orders: int


def seed(session: Session) -> SeedResult:
    """Insert anything missing from the catalogue; never duplicates or overwrites orders."""
    added = {"shops": 0, "products": 0, "customers": 0, "orders": 0}
    products_by_slug: dict[str, Product] = {}

    for spec in catalog.SHOPS:
        shop = session.scalar(select(Shop).where(Shop.slug == spec["slug"]))
        if shop is None:
            shop = Shop(slug=spec["slug"], name=spec["name"], category=spec["category"],
                        floor=spec["floor"], position=spec["position"],
                        description=spec["description"])  # fmt: skip
            session.add(shop)
            added["shops"] += 1
        for p in spec["products"]:
            product = session.scalar(select(Product).where(Product.slug == p["slug"]))
            if product is None:
                product = Product(slug=p["slug"], name=p["name"], color=p["color"],
                                  price_cents=p["price_cents"], variant_label=p["variant_label"],
                                  description=p["description"], image_url=p["image_url"],
                                  image_credit=p["image_credit"],
                                  variants=[Variant(label=v, stock=catalog.DEFAULT_STOCK)
                                            for v in p["variants"]])  # fmt: skip
                shop.products.append(product)
                added["products"] += 1
            elif product.image_url is None and p["image_url"]:
                # Older databases: fill in photos without touching prices or stock.
                product.image_url, product.image_credit = (
                    p["image_url"],
                    p["image_credit"],
                )
                product.description = product.description or p["description"]
            products_by_slug[p["slug"]] = product

    customers: dict[str, Customer] = {}
    for c in catalog.CUSTOMERS:
        customer = session.scalar(select(Customer).where(Customer.email == c["email"]))
        if customer is None:
            customer = Customer(**c)
            session.add(customer)
            added["customers"] += 1
        customers[c["email"]] = customer

    for code, email, status, lines in catalog.ORDERS:
        if session.scalar(select(Order).where(Order.code == code)) is not None:
            continue
        items = [
            OrderItem(product=products_by_slug[slug], variant_label=variant, quantity=qty,
                      unit_price_cents=products_by_slug[slug].price_cents)
            for slug, variant, qty in lines
        ]  # fmt: skip
        total = sum(i.unit_price_cents * i.quantity for i in items)
        session.add(Order(code=code, customer=customers[email], status=status,
                          total_cents=total, items=items))  # fmt: skip
        added["orders"] += 1

    session.flush()
    return SeedResult(**added)
