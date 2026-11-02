import time
from pathlib import Path

from fastapi.testclient import TestClient

from agentrel.llm.ollama import ChatFn, ChatReply, Message, ToolCall, ToolSchema
from agentrel.web.app import create_app


def scripted_factory() -> ChatFn:
    """A fake model: verify Ali, cancel A101, then reply."""
    def chat(messages: list[Message], tools: list[ToolSchema]) -> ChatReply:
        tool_results = [m for m in messages if m["role"] == "tool"]
        if not tools:
            return ChatReply(content="90")
        if len(tool_results) == 0:
            return ChatReply(content="", tool_calls=[ToolCall(name="verify_customer",
                             arguments={"email": "ali@example.com", "zip": "54000"})])
        if len(tool_results) == 1:
            return ChatReply(content="", tool_calls=[ToolCall(name="cancel_order", arguments={"order_id": "A101"})])
        return ChatReply(content="thinking...\nReply: Your order A101 is cancelled.")
    return chat


def client(tmp_path: Path) -> TestClient:
    return TestClient(create_app(chat_factory=lambda model: scripted_factory(), results_dir=tmp_path))


def test_pages_served(tmp_path: Path) -> None:
    c = client(tmp_path)
    assert "TechMart" in c.get("/").text
    assert "agentrel" in c.get("/dashboard").text
    assert c.get("/static/style.css").status_code == 200


def test_store_state_lists_orders_and_customers(tmp_path: Path) -> None:
    data = client(tmp_path).get("/api/store").json()
    assert "A101" in data["orders"] and data["customers"][0]["email"] == "ali@example.com"


def test_chat_runs_tools_and_keeps_session(tmp_path: Path) -> None:
    c = client(tmp_path)
    res = c.post("/api/chat", json={"message": "I'm Ali, cancel A101"}).json()
    assert res["reply"] == "Your order A101 is cancelled."
    assert res["orders"]["A101"]["status"] == "cancelled"
    assert res["violations"] == []
    assert [s["role"] for s in res["steps"]] == ["assistant", "tool", "assistant", "tool", "assistant"]
    state = c.get("/api/store", params={"session_id": res["session_id"]}).json()
    assert state["orders"]["A101"]["status"] == "cancelled"
    fresh = c.get("/api/store").json()
    assert fresh["orders"]["A101"]["status"] == "pending"  # other visitors unaffected


def test_chat_rejects_empty_message(tmp_path: Path) -> None:
    assert client(tmp_path).post("/api/chat", json={"message": ""}).status_code == 422


def test_evaluation_job_produces_report(tmp_path: Path) -> None:
    c = client(tmp_path)
    job = c.post("/api/evaluations", json={"model": "fake", "n_runs": 1, "fault_probability": 0}).json()
    for _ in range(100):
        data = c.get("/api/evaluations").json()
        if data["jobs"][0]["status"] != "running":
            break
        time.sleep(0.05)
    assert data["jobs"][0]["status"] == "done", data["jobs"]
    name = data["reports"][0]["name"]
    detail = c.get(f"/api/evaluations/{name}").json()
    assert {"baseline", "environment", "prompt"} <= set(detail["runs"])
    assert detail["report"]["agent_name"] == name
    assert job["status"] == "running"


def test_evaluation_detail_blocks_path_tricks(tmp_path: Path) -> None:
    c = client(tmp_path)
    assert c.get("/api/evaluations/..").status_code == 404
    assert c.get("/api/evaluations/missing").status_code == 404
