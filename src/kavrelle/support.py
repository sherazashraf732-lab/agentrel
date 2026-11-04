"""Kavrelle Support: the customer chat, answered by the agentrel tool agent.

Safety lives in the tools, not in the prompt. The model can ask to cancel or refund,
but the tools act only for the customer verified in *this* conversation, through the
same shop rules as the website. A model that is confused, or a customer who tries to
talk it round, still cannot touch someone else's order or break the store policy.

Replies run in a single background worker (a laptop serves one model at a time);
the browser polls a job for short status steps. The customer never sees which model
answers: replies are scrubbed of vendor names and the model is configured server-side.
"""

from __future__ import annotations

import logging
import os
import re
import threading
import time
import uuid
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from agentrel.agents.tool_agent import Tool, run_turn
from agentrel.llm.ollama import ChatFn, Message
from kavrelle import shop
from kavrelle.db import session_scope
from kavrelle.models import Conversation, Customer, Order, Product, ToolCall
from kavrelle.models import Message as MessageRow

log = logging.getLogger("kavrelle.support")
MAX_MESSAGE = 1000
MAX_HISTORY = 12
MAX_QUEUE = 20
JOB_TTL_S = 15 * 60
OFFLINE_REPLY = (
    "Sorry, our support assistant is unavailable right now. You can still track or cancel "
    "an order from “Track order” at the top of the page."
)
SLOW_REPLY = "Sorry, that took too long on our side. Please send your message again."
VAGUE = (
    "reply to",
    "i'd love to know",
    "i'd be happy to help",
    "how can i help",
    "sure, i can help",
)
UNSURE_REPLY = "Sorry, I couldn’t finish that. Could you say it another way?"
STATUS_WORDS = ("pending", "shipped", "delivered", "cancelled", "refunded")
CLAIM_RE = re.compile(
    r"\b(?:is|has been|was|been|now)\s+(?:now\s+)?(pending|shipped|delivered|cancell?ed|canceled|refunded)\b"
)
DOUBT_RE = re.compile(
    r"\b(error|apolog\w*|unable|can't|can’t|cannot|can not|not able to|unfortunately|not possible|went wrong)\b"
)
EXPLAIN = {
    "pending": "it’s placed and not shipped yet, so you can still cancel it. Just ask.",
    "shipped": "it’s on its way to you.",
    "delivered": "it has been delivered. If something is wrong, you can ask for a refund.",
    "cancelled": "it was cancelled, so nothing will be charged.",
    "refunded": "it has been refunded.",
}

SYSTEM_PROMPT = """You are Kavrelle Support, the customer assistant of Kavrelle, a 3D shopping mall.
You help with orders (status, cancellations, refunds) and with finding products.

How to work:
- For any order question, first call verify_customer(email, zip_code) unless the customer is already verified.
- Then call get_order, cancel_order or refund_order for the order they mention, and answer from the tool result.
- For shopping questions call search_products and suggest what it finds.

Rules you must always follow:
1. Only discuss or change orders that belong to the verified customer.
2. Only pending orders can be cancelled. Only delivered orders can be refunded, never above what is left.
3. Never invent order details. If a tool says NOT ALLOWED, explain kindly and stop.
4. You are Kavrelle Support. Never say which AI model, company or technology you run on.

Example:
Customer: I'm jo@example.com, zip 11111. Where is order A9?
You call verify_customer, then get_order with order_id A9, which says status shipped.
Reply: Hi Jo! Your order A9 has shipped and is on its way.

Speak directly to the customer, at most 3 short sentences, prices in US dollars.
Always end with one line that starts with "Reply:" and contains your message to the customer."""

VENDOR_RE = re.compile(
    r"\b(ollama|qwen\w*|llama[\w.:-]*|meta ai|openai|gpt-?\w*|anthropic|claude|mistral|gemma)\b",
    re.IGNORECASE,
)


def scrub(text: str) -> str:
    """Remove model and vendor names from anything shown to customers."""
    text = VENDOR_RE.sub("Kavrelle Support", text)
    return re.sub(r"(Kavrelle Support)(\s+Kavrelle Support)+", r"\1", text).strip()


STATUS = {
    "verify_customer": "Checking your details…",
    "get_order": "Looking up your order…",
    "cancel_order": "Cancelling your order…",
    "refund_order": "Processing your refund…",
    "search_products": "Searching the shops…",
}


@dataclass
class Job:
    id: str
    conversation: str
    status: str = "queued"  # queued | running | done
    step: str = "Reading your message…"
    done_steps: list[str] = field(default_factory=list)
    reply: str = ""
    offline: bool = False
    created: float = field(default_factory=time.monotonic)


class SupportError(shop.ShopError):
    kind = "invalid"


class Busy(shop.ShopError):
    kind = "busy"


@dataclass
class _Ctx:
    factory: sessionmaker[Session]
    conversation_id: int
    customer_id: int | None
    job: Job
    calls: list[tuple[str, dict[str, Any], str, bool]] = field(default_factory=list)
    summary: str = ""  # what a person would say about the last tool result
    last: tuple[str, bool, str | None] | None = (
        None  # (tool, ok, order status it established)
    )


def grounded(reply: str, ctx: _Ctx) -> str:
    """Keep the answer true to the last tool result; otherwise say the facts plainly.

    A small model sometimes contradicts what the tool just returned ("there was an error",
    "pending orders can't be changed") or claims an action that the store refused.
    """
    if ctx.last is None or not ctx.summary:
        return reply
    tool, ok, status = ctx.last
    low = reply.lower()
    claims = {
        "cancelled" if c.startswith("cancel") else c for c in CLAIM_RE.findall(low)
    }
    doubt = DOUBT_RE.search(low) is not None
    if (
        ok
        and status
        and (
            doubt
            or (claims and claims != {status})
            or (not claims and status not in low)
        )
    ):
        return ctx.summary
    if (
        not ok
        and tool in ("cancel_order", "refund_order")
        and claims & {"cancelled", "refunded"}
    ):
        return ctx.summary
    return reply


def _identity(s: Session, ctx: _Ctx) -> Customer | None:
    return s.get(Customer, ctx.customer_id) if ctx.customer_id else None


def make_tools(ctx: _Ctx) -> dict[str, Tool]:
    """Website tools. Unlike the evaluation store, these ENFORCE the policy."""

    def record(
        name: str,
        args: dict[str, Any],
        result: str,
        ok: bool,
        done: str | None = None,
        say: str | None = None,
    ) -> str:
        ctx.calls.append((name, args, result, ok))
        if say:
            ctx.summary = say
        found = re.search(
            r"\bis (?:now )?(pending|shipped|delivered|cancelled|refunded)\b", result
        )
        ctx.last = (
            name,
            ok,
            found.group(1)
            if ok and found and name in ("get_order", "cancel_order")
            else None,
        )
        if ok and done and done not in ctx.job.done_steps:
            ctx.job.done_steps.append(done)
        return result

    def step(name: str) -> None:
        ctx.job.step = STATUS.get(name, "Working on it…")

    def verify_customer(email: str = "", zip_code: str = "") -> str:
        step("verify_customer")
        args = {"email": email, "zip_code": zip_code}
        with session_scope(ctx.factory) as s:
            c = s.scalar(
                select(Customer).where(Customer.email == str(email).strip().lower())
            )
            if c is None or c.zip_code != str(zip_code).strip():
                return record(
                    "verify_customer",
                    args,
                    "No customer matches that email and zip code.",
                    False,
                    say="I couldn't find an account with that email and zip code. Could you check them?",
                )
            ctx.customer_id = c.id
            conv = s.get(Conversation, ctx.conversation_id)
            if conv is not None:
                conv.customer_id = c.id
            orders = ", ".join(
                f"{o.code} is {o.status} (${o.total_cents / 100:.2f})"
                for o in s.scalars(select(Order).where(Order.customer_id == c.id))
            )
            first = c.name.split()[0]
            return record("verify_customer", args, f"Verified: {c.name}. Their orders: {orders or 'none'}.", True,
                          "Identity confirmed",
                          say=f"Thanks {first}, you're verified. Your orders: {orders or 'none yet'}.")  # fmt: skip

    def _order_text(o: Order) -> str:
        items = "; ".join(
            f"{i.quantity} x {i.product.name} ({i.variant_label}) ${i.unit_price_cents / 100:.2f}"
            for i in o.items
        )
        return (f"Order {o.code} is {o.status}. Total ${o.total_cents / 100:.2f}, "
                f"refunded ${o.refunded_cents / 100:.2f}. Items: {items}.")  # fmt: skip

    def _guarded(
        name: str,
        args: dict[str, Any],
        act: Callable[[Session, Customer], str],
        done: str,
        friendly: Callable[[str], str] | None = None,
    ) -> str:
        step(name)
        with session_scope(ctx.factory) as s:
            who = _identity(s, ctx)
            if who is None:
                return record(
                    name,
                    args,
                    "NOT ALLOWED: verify the customer first with verify_customer.",
                    False,
                )
            try:
                result = act(s, who)
                say = friendly(result) if friendly else result.removeprefix("Done. ")
                return record(name, args, result, True, done, say=say)
            except shop.NotFound:
                s.rollback()
                msg = ("NOT ALLOWED: no order with that code belongs to this customer. "
                       "Say you can only help with their own orders; do not guess anything else.")  # fmt: skip
                return record(
                    name,
                    args,
                    msg,
                    False,
                    say="I can only help with orders on your own account.",
                )
            except shop.ShopError as e:
                s.rollback()
                return record(
                    name,
                    args,
                    f"NOT ALLOWED: {e.message}",
                    False,
                    say=f"Sorry, I can't do that: {e.message}.",
                )

    def oid(x: object) -> str:
        return str(x).strip().lstrip("#").upper()

    def _friendly_order(result: str) -> str:
        m = re.match(r"Order (\S+) is (\w+)\. Total (\$[\d.,]+)", result)
        if not m:
            return result
        code, status, total = m.groups()
        return f"Your order {code} is {status}: {EXPLAIN.get(status, '')} Total {total}.".replace(
            ":  ", ". "
        )

    def get_order(order_id: str = "") -> str:
        return _guarded("get_order", {"order_id": order_id},
                        lambda s, c: _order_text(shop.find_order(s, oid(order_id), c.email, c.zip_code)), "Order found",
                        _friendly_order)  # fmt: skip

    def cancel_order(order_id: str = "") -> str:
        def act(s: Session, c: Customer) -> str:
            o = shop.cancel_order(s, oid(order_id), c.email, c.zip_code)
            return f"Done. Order {o.code} is now cancelled."

        return _guarded("cancel_order", {"order_id": order_id}, act, "Order cancelled")

    def refund_order(order_id: str = "", amount: float | str = 0) -> str:
        def act(s: Session, c: Customer) -> str:
            try:
                cents = round(float(str(amount).replace("$", "").strip()) * 100)
            except ValueError as e:
                raise shop.Invalid("Refund amount must be a number") from e
            o = shop.refund_order(s, oid(order_id), c.email, c.zip_code, cents)
            return f"Done. Refunded ${cents / 100:.2f} on order {o.code} (status {o.status})."

        return _guarded(
            "refund_order",
            {"order_id": order_id, "amount": amount},
            act,
            "Refund started",
        )

    def search_products(query: str = "") -> str:
        step("search_products")
        words = [w for w in str(query).lower().split() if len(w) > 1][:6]
        with session_scope(ctx.factory) as s:
            found = []
            for p in s.scalars(select(Product).where(Product.active.is_(True))):
                hay = f"{p.name} {p.color} {p.description} {p.shop.name} {p.shop.category}".lower()
                score = sum(w in hay for w in words)
                if score:
                    found.append((score, p))
            found.sort(key=lambda t: -t[0])
            top = [
                f"{p.name} (${p.price_cents / 100:.2f}, {p.shop.name}, floor {p.shop.floor})"
                for _, p in found[:4]
            ]
        return record(
            "search_products",
            {"query": query},
            "; ".join(top) or "No matching products.",
            True,
        )

    string = {"type": "string"}
    return {
        t.name: t for t in [
            Tool("verify_customer", "Verify the customer by email and zip code.",
                 {"type": "object", "properties": {"email": string, "zip_code": string}, "required": ["email", "zip_code"]},
                 verify_customer),
            Tool("get_order", "Get the status and items of one of the verified customer's orders.",
                 {"type": "object", "properties": {"order_id": string}, "required": ["order_id"]}, get_order),
            Tool("cancel_order", "Cancel a pending order of the verified customer.",
                 {"type": "object", "properties": {"order_id": string}, "required": ["order_id"]}, cancel_order),
            Tool("refund_order", "Refund part or all of a delivered order of the verified customer. Amount in dollars.",
                 {"type": "object", "properties": {"order_id": string, "amount": {"type": "number"}},
                  "required": ["order_id", "amount"]}, refund_order),
            Tool("search_products", "Find products in the mall by keywords, e.g. 'black dress'.",
                 {"type": "object", "properties": {"query": string}, "required": ["query"]}, search_products),
        ]
    }  # fmt: skip


class SupportDesk:
    """Owns the worker thread and the in-memory job table."""

    def __init__(self, factory: sessionmaker[Session], chat_factory: Callable[[], ChatFn],
                 timeout_s: float = 240.0, max_steps: int = 8) -> None:  # fmt: skip
        self.factory = factory
        self.chat_factory = chat_factory
        self.timeout_s = timeout_s
        self.max_steps = max_steps
        self.jobs: dict[str, Job] = {}
        self.busy_conversations: set[str] = set()
        self._lock = threading.Lock()
        self._pool = ThreadPoolExecutor(
            max_workers=1, thread_name_prefix="kavrelle-support"
        )

    # ----- public API -----
    def submit(
        self, text: str, conversation: str | None, customer_id: int | None
    ) -> Job:
        text = (text or "").strip()
        if not text:
            raise SupportError("Please type a message")
        if len(text) > MAX_MESSAGE:
            raise SupportError(f"Please keep messages under {MAX_MESSAGE} characters")
        self._expire()
        with session_scope(self.factory) as s:
            conv = (
                s.scalar(
                    select(Conversation).where(Conversation.public_id == conversation)
                )
                if conversation
                else None
            )
            if conv is None:
                conv = Conversation(
                    public_id=str(uuid.uuid4()), customer_id=customer_id
                )
                s.add(conv)
            elif customer_id and conv.customer_id is None:
                conv.customer_id = customer_id
            s.add(MessageRow(conversation=conv, role="user", content=text))
            s.flush()
            public_id = conv.public_id or ""
        with self._lock:
            if public_id in self.busy_conversations:
                raise Busy("Please wait for the answer to your last message")
            if sum(j.status != "done" for j in self.jobs.values()) >= MAX_QUEUE:
                raise Busy(
                    "Support is very busy right now. Please try again in a minute."
                )
            job = Job(id=uuid.uuid4().hex, conversation=public_id)
            self.jobs[job.id] = job
            self.busy_conversations.add(public_id)
        self._pool.submit(self._run, job)
        return job

    def job(self, job_id: str) -> Job | None:
        job = self.jobs.get(job_id)
        if (
            job
            and job.status != "done"
            and time.monotonic() - job.created > self.timeout_s
        ):
            self._finish(job, SLOW_REPLY)
        return job

    def history(self, conversation: str) -> list[dict[str, str]]:
        with session_scope(self.factory) as s:
            conv = s.scalar(
                select(Conversation).where(Conversation.public_id == conversation)
            )
            if conv is None:
                raise shop.NotFound("Conversation not found")
            return [{"role": m.role, "content": m.content} for m in conv.messages]

    def shutdown(self) -> None:
        self._pool.shutdown(wait=False, cancel_futures=True)

    # ----- worker -----
    def _finish(self, job: Job, reply: str, offline: bool = False) -> None:
        with self._lock:
            if job.status == "done":
                return
            job.reply, job.offline, job.status, job.step = (
                scrub(reply),
                offline,
                "done",
                "",
            )
            self.busy_conversations.discard(job.conversation)

    def _expire(self) -> None:
        now = time.monotonic()
        with self._lock:
            for k in [
                k
                for k, j in self.jobs.items()
                if j.status == "done" and now - j.created > JOB_TTL_S
            ]:
                del self.jobs[k]

    def _run(self, job: Job) -> None:
        job.status = "running"
        ctx: _Ctx | None = None
        try:
            with session_scope(self.factory) as s:
                conv = s.scalar(
                    select(Conversation).where(
                        Conversation.public_id == job.conversation
                    )
                )
                assert conv is not None
                ctx = _Ctx(self.factory, conv.id, conv.customer_id, job)
                messages: list[Message] = [{"role": "system", "content": SYSTEM_PROMPT}]
                if conv.customer_id:
                    who = s.get(Customer, conv.customer_id)
                    if who is not None:
                        messages.append(
                            {
                                "role": "system",
                                "content": f"The customer is verified as {who.name} ({who.email}).",
                            }
                        )
                for m in conv.messages[-MAX_HISTORY:]:
                    messages.append({"role": m.role, "content": m.content})
            turn = run_turn(
                self.chat_factory(), make_tools(ctx), messages, self.max_steps
            )
            final = scrub(turn.final)
            # A small model sometimes stops without a usable answer: fall back to the facts.
            usable = bool(final) and not (
                final.lower().startswith(VAGUE) and len(final) < 60
            )
            reply = grounded(final, ctx) if usable else (ctx.summary or UNSURE_REPLY)
            if time.monotonic() - job.created > self.timeout_s:
                return  # the browser was already told it took too long
        except Exception:  # noqa: BLE001 - any model/network failure becomes a polite offline reply
            self._finish(job, OFFLINE_REPLY, offline=True)
            self._save(job, ctx, OFFLINE_REPLY)
            return
        self._finish(job, reply)
        self._save(job, ctx, job.reply)

    def _save(self, job: Job, ctx: _Ctx | None, reply: str) -> None:
        try:
            with session_scope(self.factory) as s:
                conv = s.scalar(
                    select(Conversation).where(
                        Conversation.public_id == job.conversation
                    )
                )
                if conv is None:
                    return
                msg = MessageRow(conversation=conv, role="assistant", content=reply)
                for name, args, result, ok in ctx.calls if ctx else []:
                    msg.tool_calls.append(
                        ToolCall(name=name, arguments=args, result=result[:2000], ok=ok)
                    )
                s.add(msg)
        except Exception:
            log.exception("could not save support reply for %s", job.conversation)


def ollama_chat_factory() -> ChatFn:
    from agentrel.llm.ollama import ollama_chat

    return ollama_chat(
        model=os.environ.get("KAVRELLE_SUPPORT_MODEL", "llama3.2:3b"),
        host=os.environ.get("KAVRELLE_OLLAMA_HOST", "http://127.0.0.1:11434"),
        timeout_s=float(os.environ.get("KAVRELLE_SUPPORT_TIMEOUT", "240")),
    )
