"""Transactional email: order confirmations, cancellations, welcome, password reset, contact.

Two transports, chosen from the environment:
- SMTP, when KAVRELLE_SMTP_HOST is set (with KAVRELLE_SMTP_PORT, _USER, _PASSWORD, and
  KAVRELLE_MAIL_FROM). Port 465 uses SSL, anything else STARTTLS.
- Otherwise an outbox folder (KAVRELLE_OUTBOX, default "outbox"): each email is written
  as a standard .eml file you can open in any mail app. Nothing leaves the machine.

Sending never blocks or breaks a purchase: emails go through one background worker and
failures are logged, not raised.
"""

from __future__ import annotations

import logging
import os
import re
import smtplib
import ssl
import threading
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from email.message import EmailMessage
from pathlib import Path

log = logging.getLogger("kavrelle.mail")
HEADER_SAFE = re.compile(r"[\r\n]+")


@dataclass(frozen=True)
class Email:
    to: str
    subject: str
    text: str


class Mailer:
    def __init__(self, outbox: Path | None = None, sender: str | None = None) -> None:
        self.sender = sender or os.environ.get(
            "KAVRELLE_MAIL_FROM", "Kavrelle <no-reply@kavrelle.local>"
        )
        self.smtp_host = os.environ.get("KAVRELLE_SMTP_HOST", "")
        self.outbox = outbox or Path(os.environ.get("KAVRELLE_OUTBOX", "outbox"))
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="kavrelle-mail"
        )
        self._lock = threading.Lock()
        self._seq = 0

    def _message(self, email: Email) -> EmailMessage:
        msg = EmailMessage()
        msg["From"] = self.sender
        msg["To"] = HEADER_SAFE.sub(" ", email.to)  # no header injection
        msg["Subject"] = HEADER_SAFE.sub(" ", email.subject)
        msg["Message-ID"] = f"<{uuid.uuid4().hex}@kavrelle.local>"
        msg.set_content(email.text)
        return msg

    def _deliver(self, email: Email) -> None:
        msg = self._message(email)
        if self.smtp_host:
            port = int(os.environ.get("KAVRELLE_SMTP_PORT", "587"))
            user, password = (
                os.environ.get("KAVRELLE_SMTP_USER", ""),
                os.environ.get("KAVRELLE_SMTP_PASSWORD", ""),
            )
            context = ssl.create_default_context()
            if port == 465:
                with smtplib.SMTP_SSL(
                    self.smtp_host, port, context=context, timeout=20
                ) as s:
                    if user:
                        s.login(user, password)
                    s.send_message(msg)
            else:
                with smtplib.SMTP(self.smtp_host, port, timeout=20) as s:
                    s.starttls(context=context)
                    if user:
                        s.login(user, password)
                    s.send_message(msg)
            return
        with self._lock:
            self.outbox.mkdir(parents=True, exist_ok=True)
            self._seq += (
                1  # files sort in the order they were sent, even within one second
            )
            name = f"{time.strftime('%Y%m%d-%H%M%S')}-{self._seq:06d}-{uuid.uuid4().hex[:8]}.eml"
            (self.outbox / name).write_bytes(bytes(msg))

    def send(self, email: Email) -> Future[None]:
        def run() -> None:
            try:
                self._deliver(email)
            except Exception:
                log.exception("could not send %r to %s", email.subject, email.to)

        return self._pool.submit(run)

    def flush(self, timeout: float = 10.0) -> None:
        """Wait for queued emails (tests and shutdown)."""
        self._pool.submit(lambda: None).result(timeout=timeout)

    def shutdown(self) -> None:
        self._pool.shutdown(wait=True)


def money(cents: int) -> str:
    return f"${cents / 100:,.2f}"


FOOTER = (
    "\n\n—\nKavrelle, the 3D mall\n"
    "This is a demonstration store: no payment is taken and nothing is shipped.\n"
    "Questions? Reply through the Contact page on the website."
)


def order_confirmation(
    name: str, to: str, code: str, lines: list[tuple[str, str, int, int]], total: int
) -> Email:
    items = "\n".join(f"  {q} x {n} ({v})  {money(p * q)}" for n, v, q, p in lines)
    return Email(to, f"Your Kavrelle order {code}",
                 f"Hi {name},\n\nThank you for your order. Here are the details.\n\nOrder code: {code}\n{items}\n"
                 f"  Total: {money(total)}\n\nTo track or cancel it, open “Track order” on the website and enter "
                 f"your order code, email and zip code. Pending orders can be cancelled at any time." + FOOTER)  # fmt: skip


def order_cancelled(name: str, to: str, code: str) -> Email:
    return Email(to, f"Order {code} is cancelled",
                 f"Hi {name},\n\nYour order {code} has been cancelled. Nothing will be charged." + FOOTER)  # fmt: skip


def order_refunded(name: str, to: str, code: str, amount: int) -> Email:
    return Email(to, f"Refund for order {code}",
                 f"Hi {name},\n\nWe have refunded {money(amount)} on order {code}." + FOOTER)  # fmt: skip


def welcome(name: str, to: str) -> Email:
    return Email(to, "Welcome to Kavrelle",
                 f"Hi {name},\n\nYour Kavrelle account is ready. Sign in any time to see your orders "
                 f"and chat with Kavrelle Support." + FOOTER)  # fmt: skip


def password_reset(name: str, to: str, link: str, minutes: int) -> Email:
    return Email(to, "Reset your Kavrelle password",
                 f"Hi {name},\n\nSomeone asked to reset the password of your Kavrelle account. If it was you, "
                 f"open this link within {minutes} minutes:\n\n{link}\n\nIf it wasn't you, ignore this email: "
                 f"your password stays the same." + FOOTER)  # fmt: skip


def contact_receipt(name: str, to: str, topic: str) -> Email:
    return Email(to, "We got your message",
                 f"Hi {name},\n\nThanks for writing to Kavrelle about “{topic}”. We will reply to this email "
                 f"address." + FOOTER)  # fmt: skip
