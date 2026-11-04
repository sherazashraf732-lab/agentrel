"""Website: TechMart store chat + agentrel reliability dashboard.

Run with `agentrel serve`, then open http://localhost:8000
- /           chat with the store's AI agent; every tool call and result is shown
- /dashboard  start a reliability evaluation and browse reports and transcripts
"""

from __future__ import annotations

import json
import threading
import uuid
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from agentrel.agents.tool_agent import run_turn
from agentrel.envs.store import CHAT_SCENARIO, CUSTOMERS, POLICY, StoreEnv
from agentrel.llm.ollama import ChatFn, Message, ollama_chat
from agentrel.pipeline import RunConfig, evaluate_store, save_results

STATIC = Path(__file__).parent / "static"


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=2000)
    session_id: str | None = None
    model: str = "llama3.2:3b"


class EvalIn(BaseModel):
    model: str = "llama3.2:3b"
    n_runs: int = Field(default=1, ge=1, le=5)
    fault_probability: float = Field(default=0.2, ge=0.0, le=1.0)


class Session:
    def __init__(self) -> None:
        self.env = StoreEnv(CHAT_SCENARIO)
        self.messages: list[Message] = [{"role": "system", "content": POLICY}]


def _safe_name(name: str, results_dir: Path) -> Path:
    """Only allow plain folder names that exist under results_dir."""
    if not name or "/" in name or "\\" in name or name.startswith("."):
        raise HTTPException(404, "not found")
    path = results_dir / name
    if not (path / "report.json").exists():
        raise HTTPException(404, "not found")
    return path


def create_app(
    chat_factory: Callable[[str], ChatFn] = ollama_chat,
    results_dir: Path = Path("agentrel-results"),
) -> FastAPI:
    app = FastAPI(title="agentrel")
    app.mount("/static", StaticFiles(directory=STATIC), name="static")
    sessions: dict[str, Session] = {}
    jobs: dict[str, dict[str, Any]] = {}
    lock = threading.Lock()

    @app.get("/", include_in_schema=False)
    def store_page() -> FileResponse:
        return FileResponse(STATIC / "store.html")

    @app.get("/dashboard", include_in_schema=False)
    def dashboard_page() -> FileResponse:
        return FileResponse(STATIC / "dashboard.html")

    @app.get("/api/store")
    def store_state(session_id: str | None = None) -> dict[str, Any]:
        session = sessions.get(session_id or "")
        orders = session.env.orders if session else StoreEnv(CHAT_SCENARIO).orders
        demo = [
            {"name": c["name"], "email": c["email"], "zip": c["zip"]}
            for c in CUSTOMERS.values()
        ]
        return {"orders": orders, "customers": demo}

    @app.post("/api/chat")
    def chat(body: ChatIn) -> dict[str, Any]:
        with lock:
            session_id = (
                body.session_id if body.session_id in sessions else uuid.uuid4().hex
            )
            session = sessions.setdefault(session_id, Session())
        session.messages.append({"role": "user", "content": body.message})
        tools = {t.name: t for t in session.env.tools()}
        try:
            turn = run_turn(chat_factory(body.model), tools, session.messages)
        except Exception as exc:
            raise HTTPException(502, f"model error: {exc}") from exc
        return {
            "session_id": session_id,
            "reply": turn.final or "(the agent stopped without replying)",
            "steps": turn.transcript,
            "orders": session.env.orders,
            "violations": session.env.violations(),
            "tokens": turn.tokens,
        }

    def _run_job(job_id: str, body: EvalIn) -> None:
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        name = f"{body.model.replace(':', '-')}-store-{stamp}"
        config = RunConfig(
            name=name,
            model=f"ollama/{body.model}",
            environment="store",
            n_runs=body.n_runs,
            fault_probability=body.fault_probability or None,
            output_dir=str(results_dir / name),
        )
        try:
            report, runs = evaluate_store(config, chat=chat_factory(body.model))
            save_results(report, runs, results_dir / name)
            jobs[job_id].update(status="done", result=name)
        except Exception as exc:  # noqa: BLE001 - surface failures on the dashboard
            jobs[job_id].update(status="failed", error=str(exc))

    @app.post("/api/evaluations")
    def start_evaluation(body: EvalIn) -> dict[str, Any]:
        job_id = uuid.uuid4().hex[:8]
        jobs[job_id] = {
            "id": job_id,
            "status": "running",
            "model": body.model,
            "started": datetime.now().isoformat(timespec="seconds"),
        }
        threading.Thread(target=_run_job, args=(job_id, body), daemon=True).start()
        return jobs[job_id]

    @app.get("/api/evaluations")
    def list_evaluations() -> dict[str, Any]:
        reports = []
        if results_dir.exists():
            for path in sorted(results_dir.glob("*/report.json"), reverse=True):
                data = json.loads(path.read_text(encoding="utf-8"))
                reports.append(
                    {
                        "name": path.parent.name,
                        "agent": data.get("agent_name"),
                        "overall": data.get("overall"),
                        "safety": data.get("safety"),
                        "accuracy": data.get("accuracy"),
                    }
                )
        return {"jobs": list(jobs.values()), "reports": reports}

    @app.get("/api/evaluations/{name}")
    def get_evaluation(name: str) -> dict[str, Any]:
        path = _safe_name(name, results_dir)
        runs: dict[str, list[Any]] = {}
        for file in sorted(path.glob("*.jsonl")):
            with file.open(encoding="utf-8") as f:
                runs[file.stem] = [json.loads(line) for line in f if line.strip()]
        report = json.loads((path / "report.json").read_text(encoding="utf-8"))
        return {"report": report, "runs": runs}

    return app
