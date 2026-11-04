"""Customer accounts: sign up, sign in, sessions.

Passwords are hashed with scrypt (standard library), compared in constant time and
never logged. Session cookies carry a random token; the database stores only its
SHA-256, so a leaked database cannot be used to sign in. Failed sign-ins are rate
limited per email and per client.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import deque
from datetime import UTC, datetime, timedelta

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from kavrelle.models import AuthSession, Customer
from kavrelle.shop import Buyer, Conflict, Invalid, _clean_buyer

SESSION_DAYS = 14
MIN_PASSWORD, MAX_PASSWORD = 8, 128
_N, _R, _P = 2**14, 8, 1
BAD_LOGIN = "Email or password is incorrect"


class RateLimited(Invalid):
    kind = "rate_limited"


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode(), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    enc = base64.b64encode
    return f"scrypt${_N}${_R}${_P}${enc(salt).decode()}${enc(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt_b64, digest_b64 = stored.split("$")
        if algo != "scrypt":
            return False
        salt, expected = base64.b64decode(salt_b64), base64.b64decode(digest_b64)
        got = hashlib.scrypt(
            password.encode(),
            salt=salt,
            n=int(n),
            r=int(r),
            p=int(p),
            dklen=len(expected),
        )
    except (ValueError, TypeError):
        return False
    return hmac.compare_digest(got, expected)


# A real hash to compare against when the email is unknown, so timing does not leak
# whether an account exists.
_DUMMY = hash_password(secrets.token_hex(8))


def _check_password(password: str) -> None:
    if not MIN_PASSWORD <= len(password) <= MAX_PASSWORD:
        raise Invalid(f"Password must be {MIN_PASSWORD} to {MAX_PASSWORD} characters")
    if password.isdigit() or password.isalpha():
        raise Invalid("Use both letters and numbers in your password")


class RateLimiter:
    """At most `limit` failures per key in `window` seconds. Thread-safe, in memory."""

    def __init__(self, limit: int, window: float) -> None:
        self.limit, self.window = limit, window
        self._hits: dict[str, deque[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> deque[float]:
        q = self._hits.setdefault(key, deque())
        while q and now - q[0] > self.window:
            q.popleft()
        return q

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            self._recent(key, time.monotonic()).append(time.monotonic())

    def reset(self, key: str) -> None:
        with self._lock:
            self._hits.pop(key, None)


def signup(session: Session, buyer: Buyer, password: str) -> Customer:
    """Create an account, or claim a guest customer record when the zip code matches."""
    buyer = _clean_buyer(buyer)
    _check_password(password)
    customer = session.scalar(select(Customer).where(Customer.email == buyer.email))
    if customer is not None:
        if customer.password_hash is not None:
            raise Conflict("An account with this email already exists. Please sign in.")
        if customer.zip_code != buyer.zip_code:
            raise Conflict("These details do not match our records")
    else:
        customer = Customer(name=buyer.name, email=buyer.email, zip_code=buyer.zip_code)
        session.add(customer)
    customer.password_hash = hash_password(password)
    session.flush()
    return customer


def login(
    session: Session, email: str, password: str, limiter: RateLimiter, client: str
) -> Customer:
    email = email.strip().lower()
    keys = (f"email:{email}", f"client:{client}")
    if any(limiter.blocked(k) for k in keys):
        raise RateLimited("Too many attempts. Please wait a few minutes and try again.")
    customer = session.scalar(select(Customer).where(Customer.email == email))
    stored = customer.password_hash if customer and customer.password_hash else _DUMMY
    ok = (
        verify_password(password[:MAX_PASSWORD], stored)
        and customer is not None
        and customer.password_hash
    )
    if not ok:
        for k in keys:
            limiter.fail(k)
        raise Invalid(BAD_LOGIN)
    limiter.reset(keys[0])
    assert customer is not None
    return customer


def _token_hash(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def start_session(session: Session, customer: Customer) -> str:
    token = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    session.add(AuthSession(token_hash=_token_hash(token), customer=customer, created_at=now,
                            expires_at=now + timedelta(days=SESSION_DAYS)))  # fmt: skip
    session.flush()
    return token


def current_customer(session: Session, token: str | None) -> Customer | None:
    if not token or len(token) > 100:
        return None
    row = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == _token_hash(token))
    )
    if row is None:
        return None
    expires = (
        row.expires_at if row.expires_at.tzinfo else row.expires_at.replace(tzinfo=UTC)
    )
    if expires < datetime.now(UTC):
        session.delete(row)
        session.flush()
        return None
    return row.customer


def end_session(session: Session, token: str | None) -> None:
    if token:
        session.execute(
            delete(AuthSession).where(AuthSession.token_hash == _token_hash(token))
        )
