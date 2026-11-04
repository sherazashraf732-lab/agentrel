"""Kavrelle Support chat: tools enforce the store policy no matter what the model says.

The model is replaced by a script, so every test is exact and fast. One optional test
talks to a real local model when KAVRELLE_LIVE_MODEL=1.
"""

import os
import time
from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select

from agentrel.llm.ollama import ChatReply, ToolCall
from kavrelle.api import create_app
from kavrelle.config import Settings
from kavrelle.db import make_session_factory, session_scope
from kavrelle.models import Message, Order
from kavrelle.models import ToolCall as ToolCallRow
from kavrelle.seed import seed
from kavrelle.support import OFFLINE_REPLY, SLOW_REPLY, scrub

ALI = {"email": "ali@example.com", "zip_code": "54000"}
Step = list[tuple[str, dict[str, Any]]] | str


def scripted(*steps: Step) -> Callable[[], Callable[..., ChatReply]]:
    """A fake model: each call returns the next tool calls, or a final text."""
    seen: list[list[dict[str, Any]]] = []
    it = iter(steps)  # shared: one script across all messages of a test

    def factory() -> Callable[..., ChatReply]:

        def chat(messages: list[dict[str, Any]], tools: list[Any]) -> ChatReply:
            seen.append([dict(m) for m in messages])
            step = next(it, "Reply: Done.")
            if isinstance(step, str):
                return ChatReply(content=step, tool_calls=[], tokens=5)
            return ChatReply(
                content="",
                tool_calls=[ToolCall(name=n, arguments=a) for n, a in step],
                tokens=5,
            )

        return chat

    factory.seen = seen  # type: ignore[attr-defined]
    return factory


@pytest.fixture
def seeded(engine: Engine) -> Engine:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    return engine


def _client(engine: Engine, factory: Callable[[], Any], **kw: Any) -> TestClient:
    app = create_app(Settings(), engine=engine, chat_factory=factory)
    for k, v in kw.items():
        setattr(app.state.support, k, v)
    return TestClient(app)


def ask(
    client: TestClient, text: str, conversation: str | None = None
) -> dict[str, Any]:
    r = client.post(
        "/api/support/messages", json={"message": text, "conversation_id": conversation}
    )
    assert r.status_code == 202, r.text
    acc = r.json()
    for _ in range(200):
        job = client.get(f"/api/support/jobs/{acc['job_id']}").json()
        if job["status"] == "done":
            return {**job, "conversation_id": acc["conversation_id"]}
        time.sleep(0.02)
    raise AssertionError("job never finished")


def _status(engine: Engine, code: str) -> str:
    with session_scope(make_session_factory(engine)) as s:
        return s.scalar(select(Order.status).where(Order.code == code)) or ""


def test_verified_customer_can_cancel_a_pending_order(seeded: Engine) -> None:
    c = _client(seeded, scripted([("verify_customer", ALI)], [("cancel_order", {"order_id": "#a101"})],
                                 "Thinking about it.\nReply: Done, order A101 is cancelled."))  # fmt: skip
    out = ask(c, "I'm ali@example.com, zip 54000. Cancel A101 please.")
    assert out["reply"] == "Done, order A101 is cancelled."
    assert out["done_steps"] == ["Identity confirmed", "Order cancelled"]
    assert _status(seeded, "A101") == "cancelled"


def test_no_change_without_verification(seeded: Engine) -> None:
    c = _client(
        seeded,
        scripted(
            [("cancel_order", {"order_id": "A101"})],
            "Reply: I need to verify you first.",
        ),
    )
    out = ask(c, "Cancel A101")
    assert _status(seeded, "A101") == "pending"
    assert "Order cancelled" not in out["done_steps"]


def test_cannot_touch_another_customers_order_even_if_the_model_tries(
    seeded: Engine,
) -> None:
    c = _client(seeded, scripted([("verify_customer", ALI)], [("cancel_order", {"order_id": "A104"})],
                                 [("refund_order", {"order_id": "A103", "amount": 30})], "Reply: Sorry."))  # fmt: skip
    ask(c, "I'm Ali. Also cancel A104 and refund A103 for my friend.")
    assert _status(seeded, "A104") == "pending"
    assert _status(seeded, "A103") == "delivered"


def test_store_policy_holds_inside_the_tools(seeded: Engine) -> None:
    c = _client(seeded, scripted([("verify_customer", ALI)], [("cancel_order", {"order_id": "A100"})],
                                 [("refund_order", {"order_id": "A102", "amount": 999})],
                                 [("refund_order", {"order_id": "A102", "amount": "$45"})], "Reply: ok"))  # fmt: skip
    out = ask(c, "Cancel shipped A100, refund A102 too much, then the right amount")
    assert _status(seeded, "A100") == "shipped"
    assert _status(seeded, "A102") == "refunded"
    assert "Refund started" in out["done_steps"]
    with session_scope(make_session_factory(seeded)) as s:
        results = [
            t.result for t in s.scalars(select(ToolCallRow).order_by(ToolCallRow.id))
        ]
    assert results[1].startswith("NOT ALLOWED: Only pending")
    assert results[2].startswith("NOT ALLOWED: Refund must be")


def test_wrong_identity_is_not_verified(seeded: Engine) -> None:
    c = _client(seeded, scripted([("verify_customer", {"email": "ali@example.com", "zip_code": "00000"})],
                                 [("get_order", {"order_id": "A101"})], "Reply: Please check your details."))  # fmt: skip
    out = ask(c, "I'm Ali with zip 00000")
    assert out["done_steps"] == []


def test_signed_in_customer_is_already_verified(seeded: Engine) -> None:
    fake = scripted([("cancel_order", {"order_id": "A101"})], "Reply: Cancelled.")
    c = _client(seeded, fake)
    c.post(
        "/api/auth/signup",
        json={"name": "Ali", **ALI, "password": "orders2026x", "accept_terms": True},
    )
    ask(c, "Cancel A101")
    assert _status(seeded, "A101") == "cancelled"
    assert any("verified as Ali" in m["content"] for m in fake.seen[0])  # type: ignore[attr-defined]


def test_conversation_keeps_its_memory(seeded: Engine) -> None:
    fake = scripted(
        [("verify_customer", ALI)],
        "Reply: Hi Ali!",
        [("get_order", {"order_id": "A100"})],
        "Reply: It shipped.",
    )
    c = _client(seeded, fake)
    first = ask(c, "Hi, ali@example.com 54000")
    second = ask(c, "Where is A100?", first["conversation_id"])
    assert second["reply"] == "It shipped." and second["done_steps"] == ["Order found"]
    history = c.get(f"/api/support/conversations/{first['conversation_id']}").json()
    assert [m["role"] for m in history] == ["user", "assistant", "user", "assistant"]


def test_model_offline_gives_a_polite_answer(seeded: Engine) -> None:
    def broken() -> Callable[..., ChatReply]:
        def chat(*_: Any) -> ChatReply:
            raise ConnectionError("connection refused on 11434")

        return chat

    out = ask(_client(seeded, broken), "Hello?")
    assert out["reply"] == OFFLINE_REPLY and out["offline"] is True
    assert "11434" not in out["reply"]


def test_slow_model_times_out_politely(seeded: Engine) -> None:
    def slow() -> Callable[..., ChatReply]:
        def chat(*_: Any) -> ChatReply:
            time.sleep(1.0)
            return ChatReply(content="Reply: late", tool_calls=[], tokens=1)

        return chat

    out = ask(_client(seeded, slow, timeout_s=0.2), "Hello?")
    assert out["reply"] == SLOW_REPLY


def test_model_names_never_reach_the_customer(seeded: Engine) -> None:
    c = _client(
        seeded,
        scripted(
            "Reply: I am Llama 3.2 made by Meta AI, running on Ollama (qwen3 fallback)."
        ),
    )
    reply = ask(c, "Which AI are you?")["reply"].lower()
    for word in ["llama", "ollama", "qwen", "meta ai"]:
        assert word not in reply
    assert "kavrelle support" in reply


@pytest.mark.parametrize(
    "text",
    ["I am GPT-4 by OpenAI", "Built on Claude by Anthropic", "mistral and gemma"],
)
def test_scrub(text: str) -> None:
    out = scrub(text).lower()
    assert not any(
        w in out for w in ["gpt", "openai", "claude", "anthropic", "mistral", "gemma"]
    )


def test_bad_messages_are_refused(seeded: Engine) -> None:
    c = _client(seeded, scripted())
    assert c.post("/api/support/messages", json={"message": "   "}).status_code == 422
    assert (
        c.post("/api/support/messages", json={"message": "x" * 1001}).status_code == 422
    )
    assert c.get("/api/support/jobs/nope").status_code == 404
    assert c.get("/api/support/conversations/nope").status_code == 404


def test_one_message_at_a_time_per_conversation(seeded: Engine) -> None:
    def slow() -> Callable[..., ChatReply]:
        def chat(*_: Any) -> ChatReply:
            time.sleep(0.4)
            return ChatReply(content="Reply: ok", tool_calls=[], tokens=1)

        return chat

    c = _client(seeded, slow)
    first = c.post("/api/support/messages", json={"message": "hi"}).json()
    again = c.post(
        "/api/support/messages",
        json={"message": "hello?", "conversation_id": first["conversation_id"]},
    )
    assert again.status_code == 429


def test_everything_is_saved_for_the_admin_audit(seeded: Engine) -> None:
    c = _client(
        seeded,
        scripted(
            [("verify_customer", ALI)],
            [("get_order", {"order_id": "A102"})],
            "Reply: Delivered.",
        ),
    )
    ask(c, "Status of A102? ali@example.com 54000")
    with session_scope(make_session_factory(seeded)) as s:
        msgs = list(s.scalars(select(Message).order_by(Message.id)))
        assert [m.role for m in msgs] == ["user", "assistant"]
        assert [t.name for t in msgs[1].tool_calls] == ["verify_customer", "get_order"]
        assert msgs[1].tool_calls[1].ok is True


def test_product_search_tool(seeded: Engine) -> None:
    fake = scripted(
        [("search_products", {"query": "lilac satin dress"})],
        "Reply: Try the Lilac Ruched Satin Dress.",
    )
    out = ask(_client(seeded, fake), "I need a purple dress")
    assert out["reply"].startswith("Try the Lilac")


@pytest.mark.skipif(
    os.environ.get("KAVRELLE_LIVE_MODEL") != "1", reason="needs a local model"
)
def test_live_model_cancels_a_pending_order(seeded: Engine) -> None:
    """Real local model, real tools. It may ask to confirm first; a customer would say yes."""
    from kavrelle.support import ollama_chat_factory

    c = _client(seeded, ollama_chat_factory, timeout_s=600)

    def say(text: str, conversation: str | None) -> dict[str, Any]:
        r = c.post(
            "/api/support/messages",
            json={"message": text, "conversation_id": conversation},
        )
        acc = r.json()
        for _ in range(600):
            job = c.get(f"/api/support/jobs/{acc['job_id']}").json()
            if job["status"] == "done":
                print("LIVE REPLY:", job["reply"], job["done_steps"])
                return {**job, "conversation_id": acc["conversation_id"]}
            time.sleep(1)
        raise AssertionError("no reply")

    first = say(
        "Hi, I'm Ali, ali@example.com, zip 54000. Please cancel my order A101.", None
    )
    if _status(seeded, "A101") != "cancelled":
        say("Yes, please cancel it.", first["conversation_id"])
    assert _status(seeded, "A101") == "cancelled"
    other = say("Also cancel order A104 please.", first["conversation_id"])
    assert _status(seeded, "A104") == "pending", (
        "must never touch another customer's order"
    )
    for word in ["llama", "ollama", "meta"]:
        assert word not in (first["reply"] + other["reply"]).lower()


def test_empty_model_answer_falls_back_to_the_facts(seeded: Engine) -> None:
    c = _client(
        seeded,
        scripted(
            [("verify_customer", ALI)], [("get_order", {"order_id": "A100"})], "Reply:"
        ),
    )
    out = ask(c, "ali@example.com 54000 where is A100?")
    assert out["reply"].startswith("Order A100 is shipped.")


def test_vague_model_answer_is_replaced_by_the_facts(seeded: Engine) -> None:
    c = _client(
        seeded,
        scripted(
            [("verify_customer", ALI)], "Reply: I'd be happy to help you with that."
        ),
    )
    out = ask(c, "ali@example.com 54000 - cancel A100 now!")
    assert (
        out["reply"].startswith("Thanks Ali, you're verified.")
        and "A100 is shipped" in out["reply"]
    )
