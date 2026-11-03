"""Shopping rules: cart, checkout, and the order policies the support assistant must follow.

Every rule lives here, in one place, so the website and the assistant's tools enforce
exactly the same policy (the agentrel TechMart rules):
  1. Identity must be verified (email + zip) before an order is shown or changed.
  2. A customer only sees and changes their own orders.
  3. Only pending orders can be cancelled.
  4. Only delivered orders can be refunded, never above what is left to refund.
"""

from __future__ import annotations

import re
import secrets
import string
import uuid
from dataclasses import dataclass

from sqlalchemy import select, update
from sqlalchemy.orm import Session, selectinload

from kavrelle.models import (
    MAX_LINE_QUANTITY,
    Cart,
    CartItem,
    Customer,
    Order,
    OrderItem,
    Product,
    Variant,
)

EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
ZIP_RE = re.compile(r"^[A-Za-z0-9 -]{3,12}$")


class ShopError(Exception):
    """Base class; `kind` maps to an HTTP status in the API."""

    kind = "invalid"

    def __init__(self, message: str) -> None:
        super().__init__(message)
        self.message = message


class NotFound(ShopError):
    kind = "not_found"


class Invalid(ShopError):
    kind = "invalid"


class Conflict(ShopError):
    kind = "conflict"


# ---------- cart ----------


def create_cart(session: Session) -> Cart:
    cart = Cart(token=str(uuid.uuid4()))
    session.add(cart)
    session.flush()
    return cart


def get_cart(session: Session, token: str) -> Cart:
    cart = session.scalar(
        select(Cart)
        .where(Cart.token == token)
        .options(
            selectinload(Cart.items)
            .selectinload(CartItem.variant)
            .selectinload(Variant.product)
        )
    )
    if cart is None:
        raise NotFound("Cart not found")
    return cart


def _check_quantity(quantity: int) -> None:
    if not 1 <= quantity <= MAX_LINE_QUANTITY:
        raise Invalid(f"Quantity must be between 1 and {MAX_LINE_QUANTITY}")


def add_item(
    session: Session, token: str, product_slug: str, variant: str, quantity: int
) -> Cart:
    """Add to cart; the same product+variant is merged into one line."""
    _check_quantity(quantity)
    cart = get_cart(session, token)
    found = session.scalar(
        select(Variant)
        .join(Product)
        .where(
            Product.slug == product_slug,
            Product.active.is_(True),
            Variant.label == variant,
        )
    )
    if found is None:
        raise NotFound("Product or option not found")
    line = next((i for i in cart.items if i.variant_id == found.id), None)
    new_qty = quantity + (line.quantity if line else 0)
    _check_quantity(new_qty)
    if new_qty > found.stock:
        raise Conflict(f"Only {found.stock} left in stock")
    if line:
        line.quantity = new_qty
    else:
        cart.items.append(CartItem(variant=found, quantity=quantity))
    session.flush()
    return cart


def set_quantity(session: Session, token: str, item_id: int, quantity: int) -> Cart:
    """Change a line; quantity 0 removes it."""
    cart = get_cart(session, token)
    line = next((i for i in cart.items if i.id == item_id), None)
    if line is None:
        raise NotFound("Cart item not found")
    if quantity == 0:
        cart.items.remove(line)
    else:
        _check_quantity(quantity)
        if quantity > line.variant.stock:
            raise Conflict(f"Only {line.variant.stock} left in stock")
        line.quantity = quantity
    session.flush()
    return cart


def cart_total_cents(cart: Cart) -> int:
    return sum(i.variant.product.price_cents * i.quantity for i in cart.items)


# ---------- checkout ----------


@dataclass(frozen=True)
class Buyer:
    name: str
    email: str
    zip_code: str


def _clean_buyer(buyer: Buyer) -> Buyer:
    name, email, zip_code = (
        buyer.name.strip(),
        buyer.email.strip().lower(),
        buyer.zip_code.strip(),
    )
    if not 1 <= len(name) <= 80:
        raise Invalid("Please enter your name")
    if not EMAIL_RE.match(email) or len(email) > 160:
        raise Invalid("Please enter a valid email")
    if not ZIP_RE.match(zip_code):
        raise Invalid("Please enter a valid zip code")
    return Buyer(name, email, zip_code)


def _new_order_code(session: Session) -> str:
    alphabet = string.ascii_uppercase + string.digits
    while True:
        code = "K" + "".join(secrets.choice(alphabet) for _ in range(7))
        if session.scalar(select(Order.id).where(Order.code == code)) is None:
            return code


def checkout(session: Session, token: str, buyer: Buyer) -> Order:
    """Turn the cart into a pending order, take the stock, empty the cart. All or nothing."""
    buyer = _clean_buyer(buyer)
    cart = get_cart(session, token)
    if not cart.items:
        raise Invalid("Your cart is empty")

    customer = session.scalar(select(Customer).where(Customer.email == buyer.email))
    if customer is None:
        customer = Customer(name=buyer.name, email=buyer.email, zip_code=buyer.zip_code)
        session.add(customer)
    elif customer.zip_code != buyer.zip_code:
        raise Conflict("These details do not match our records")

    items: list[OrderItem] = []
    for line in cart.items:
        # Atomic: only succeeds if enough stock is still there, so two shoppers can't oversell.
        taken = session.execute(
            update(Variant)
            .where(Variant.id == line.variant_id, Variant.stock >= line.quantity)
            .values(stock=Variant.stock - line.quantity)
        )
        if taken.rowcount != 1:  # type: ignore[attr-defined]
            raise Conflict(
                f"Sorry, {line.variant.product.name} ({line.variant.label}) just sold out"
            )
        items.append(
            OrderItem(
                product_id=line.variant.product_id,
                variant_label=line.variant.label,
                quantity=line.quantity,
                unit_price_cents=line.variant.product.price_cents,
            )
        )
    order = Order(
        code=_new_order_code(session),
        customer=customer,
        status="pending",
        total_cents=sum(i.unit_price_cents * i.quantity for i in items),
        items=items,
    )
    session.add(order)
    cart.items.clear()
    session.flush()
    return order


# ---------- orders (policy-enforced) ----------


def find_order(session: Session, code: str, email: str, zip_code: str) -> Order:
    """Rules 1 and 2: wrong details look exactly like a missing order (no information leak)."""
    order = session.scalar(
        select(Order)
        .where(Order.code == code.strip().upper())
        .options(selectinload(Order.items).selectinload(OrderItem.product))
    )
    if (
        order is None
        or order.customer.email != email.strip().lower()
        or order.customer.zip_code != zip_code.strip()
    ):
        raise NotFound("Order not found")
    return order


def cancel_order(session: Session, code: str, email: str, zip_code: str) -> Order:
    """Rule 3; the stock goes back on the shelf."""
    order = find_order(session, code, email, zip_code)
    if order.status != "pending":
        raise Conflict(
            f"Only pending orders can be cancelled (this one is {order.status})"
        )
    for item in order.items:
        session.execute(
            update(Variant)
            .where(
                Variant.product_id == item.product_id,
                Variant.label == item.variant_label,
            )
            .values(stock=Variant.stock + item.quantity)
        )
    order.status = "cancelled"
    session.flush()
    return order


def refund_order(
    session: Session, code: str, email: str, zip_code: str, amount_cents: int
) -> Order:
    """Rule 4; a full refund marks the order refunded."""
    order = find_order(session, code, email, zip_code)
    if order.status != "delivered":
        raise Conflict(
            f"Only delivered orders can be refunded (this one is {order.status})"
        )
    left = order.total_cents - order.refunded_cents
    if amount_cents <= 0 or amount_cents > left:
        raise Invalid(f"Refund must be between 0.01 and {left / 100:.2f}")
    order.refunded_cents += amount_cents
    if order.refunded_cents == order.total_cents:
        order.status = "refunded"
    session.flush()
    return order
