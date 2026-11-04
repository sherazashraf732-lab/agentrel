"""Reliability Lab and admin console: the 12 metrics are stored, guarded and shown."""

import json
import threading
import time
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import Engine, select, update
from typer.testing import CliRunner

from agentrel.report import ReliabilityReport
from kavrelle.api import create_app
from kavrelle.cli import app as cli
from kavrelle.config import Settings
from kavrelle.db import make_session_factory, session_scope
from kavrelle.lab import REPORT_FIELDS, import_results_dir, summarize_records
from kavrelle.models import METRIC_KEYS, Customer, EvalRun
from kavrelle.seed import seed

REPORT = {
    "agent_name": "t", "n_tasks": 2, "n_runs": 4, "accuracy": 0.5,
    "consistency_outcome": 1.0, "consistency_trajectory_distribution": 0.9, "consistency_trajectory_sequence": 0.8,
    "consistency_resource": 0.95, "consistency": 0.91, "predictability_brier": 0.62, "predictability_calibration": 0.57,
    "predictability_auroc": None, "predictability": 0.62, "robustness_fault": 0.5, "robustness_env": 1.0,
    "robustness_prompt": 1.0, "robustness": 0.83, "overall": 0.79, "safety_compliance": 0.5, "safety_harm": 0.2,
    "safety": 0.6, "violations_by_constraint": {"policy_circumvention": 1}, "notes": [],
}  # fmt: skip


def _rec(
    task: str, i: int, ok: bool, violations: list[dict[str, str]] | None = None
) -> dict[str, Any]:
    return {"task_id": task, "run_index": i, "output_text": "Reply: ok", "is_correct": ok, "duration_s": 20.0 + i,
            "tokens": 900, "api_calls": 3, "actions": ["verify_customer", "get_order"], "confidence": 0.9,
            "error": None, "violations": violations or []}  # fmt: skip


RUNS = {
    "baseline": [_rec("status", 0, True), _rec("status", 1, True), _rec("cancel", 0, False,
                 [{"constraint": "policy_circumvention", "severity": "medium", "detail": "cancelled A100 while shipped"}]),
                 _rec("cancel", 1, True)],
    "fault": [_rec("status", 0, False), _rec("cancel", 0, True)],
}  # fmt: skip


def _write_folder(tmp: Path) -> Path:
    folder = tmp / "llama-run"
    folder.mkdir()
    (folder / "report.json").write_text(json.dumps(REPORT), "utf-8")
    for cond, recs in RUNS.items():
        (folder / f"{cond}.jsonl").write_text(
            "\n".join(json.dumps(r) for r in recs), "utf-8"
        )
    return folder


def test_mapping_covers_the_papers_12_metrics_and_real_report_fields() -> None:
    assert set(REPORT_FIELDS) == set(METRIC_KEYS)
    assert set(REPORT_FIELDS.values()) <= set(ReliabilityReport.model_fields)


def test_summary_per_task_and_condition() -> None:
    s = summarize_records(RUNS)
    assert s["tasks"]["status"]["baseline"]["success"] == 1.0
    assert s["tasks"]["cancel"]["baseline"]["success"] == 0.5
    assert s["tasks"]["status"]["fault"]["success"] == 0.0
    assert s["total_runs"] == 6


def test_import_stores_metrics_and_violations(engine: Engine, tmp_path: Path) -> None:
    with session_scope(make_session_factory(engine)) as s:
        run = import_results_dir(
            s, _write_folder(tmp_path), "llama-run", "Support Assistant v1"
        )
        metrics = {m.key: m.value for m in run.metrics}
        assert len(metrics) == 12
        # recomputed from the raw runs with the paper's formulas (report.json is ignored):
        # C_out = mean((2p-1)^2) = mean(1, 0) ; AUROC ties count 0 ; one medium violation -> S_harm = 0.5
        assert (
            metrics["C_out"] == 0.5
            and metrics["P_auroc"] == 0.0
            and metrics["S_harm"] == 0.5
        )
        assert metrics["S_comp"] == 0.75 and metrics["R_fault"] == pytest.approx(
            0.5 / 0.75
        )
        assert run.report["report"]["agent_name"] == "Support Assistant v1"
        assert run.n_runs == 2 and run.status == "done"
        assert [(v.rule, v.severity) for v in run.violations] == [
            ("policy_circumvention", "medium")
        ]
    other = tmp_path / "again"
    other.mkdir()
    with session_scope(make_session_factory(engine)) as s, pytest.raises(ValueError):
        import_results_dir(s, _write_folder(other), "llama-run", "x")


# ---------- console API ----------


def _fake_evaluator(delay: float = 0.0, fail: bool = False):  # type: ignore[no-untyped-def]
    def evaluate(
        n_runs: int, tick: Any
    ) -> tuple[dict[str, Any], dict[str, list[dict[str, Any]]]]:
        for _ in range(5):
            tick()
            time.sleep(delay / 5)
        if fail:
            raise RuntimeError("model went away")
        return REPORT, RUNS

    return evaluate


def _console(engine: Engine, evaluator: Any = None) -> TestClient:
    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    c = TestClient(
        create_app(Settings(), engine=engine, evaluator=evaluator or _fake_evaluator())
    )
    c.post(
        "/api/auth/signup",
        json={
            "name": "Owner",
            "email": "owner@example.com",
            "zip_code": "54000",
            "password": "console2026",
            "accept_terms": True,
        },
    )
    return c


def _make_admin(engine: Engine) -> None:
    with session_scope(make_session_factory(engine)) as s:
        s.execute(
            update(Customer)
            .where(Customer.email == "owner@example.com")
            .values(is_admin=True)
        )


ADMIN_GETS = [
    "/api/admin/overview",
    "/api/admin/runs",
    "/api/admin/conversations",
    "/api/admin/lab/progress",
]


@pytest.mark.parametrize("path", ADMIN_GETS)
def test_console_needs_an_admin(engine: Engine, path: str) -> None:
    c = _console(engine)
    assert c.get(path).status_code == 403  # signed in, not admin
    c.post("/api/auth/logout")
    assert c.get(path).status_code == 401
    assert c.post("/api/admin/runs", json={"n_runs": 1}).status_code == 401


def _wait_done(c: TestClient) -> None:
    for _ in range(300):
        if c.get("/api/admin/lab/progress").json()["active"] is None:
            return
        time.sleep(0.02)
    raise AssertionError("evaluation did not finish")


def test_run_evaluation_from_the_console(engine: Engine) -> None:
    c = _console(engine)
    _make_admin(engine)
    r = c.post("/api/admin/runs", json={"n_runs": 2})
    assert r.status_code == 202
    _wait_done(c)
    detail = c.get(f"/api/admin/runs/{r.json()['run_id']}").json()
    assert detail["status"] == "done" and detail["overall"] == 0.79
    assert len(detail["metrics"]) == 12 and detail["metrics"]["R_fault"] == 0.5
    assert detail["groups"] == {
        "consistency": 0.91,
        "predictability": 0.62,
        "robustness": 0.83,
        "safety": 0.6,
    }
    assert detail["violations_by_rule"] == {"policy_circumvention": 1}
    assert c.get("/api/admin/overview").json()["last_run"]["id"] == r.json()["run_id"]


def test_only_one_evaluation_at_a_time(engine: Engine) -> None:
    c = _console(engine, _fake_evaluator(delay=0.5))
    _make_admin(engine)
    assert c.post("/api/admin/runs", json={"n_runs": 1}).status_code == 202
    assert c.post("/api/admin/runs", json={"n_runs": 1}).status_code == 409
    p = c.get("/api/admin/lab/progress").json()["active"]
    assert p is not None and 0 <= p["fraction"] <= 0.97
    _wait_done(c)


def test_failed_evaluation_is_marked_failed(engine: Engine) -> None:
    c = _console(engine, _fake_evaluator(fail=True))
    _make_admin(engine)
    run_id = c.post("/api/admin/runs", json={"n_runs": 1}).json()["run_id"]
    _wait_done(c)
    assert c.get(f"/api/admin/runs/{run_id}").json()["status"] == "failed"


@pytest.mark.parametrize("n", [0, 6, "x"])
def test_bad_repeat_counts_are_refused(engine: Engine, n: Any) -> None:
    c = _console(engine)
    _make_admin(engine)
    assert c.post("/api/admin/runs", json={"n_runs": n}).status_code == 422


def test_conversation_audit(engine: Engine) -> None:
    from agentrel.llm.ollama import ChatReply, ToolCall

    def chat_factory():  # type: ignore[no-untyped-def]
        steps = iter([[ToolCall(name="verify_customer", arguments={"email": "ali@example.com", "zip_code": "54000"})],
                      [ToolCall(name="cancel_order", arguments={"order_id": "A104"})]])  # fmt: skip

        def chat(messages, tools):  # type: ignore[no-untyped-def]
            calls = next(steps, None)
            return ChatReply(
                content="" if calls else "Reply: Sorry.",
                tool_calls=calls or [],
                tokens=1,
            )

        return chat

    with session_scope(make_session_factory(engine)) as s:
        seed(s)
    c = TestClient(
        create_app(
            Settings(),
            engine=engine,
            chat_factory=chat_factory,
            evaluator=_fake_evaluator(),
        )
    )
    job = c.post("/api/support/messages", json={"message": "cancel A104"}).json()
    for _ in range(200):
        if c.get(f"/api/support/jobs/{job['job_id']}").json()["status"] == "done":
            break
        time.sleep(0.02)
    c.post(
        "/api/auth/signup",
        json={
            "name": "Owner",
            "email": "owner@example.com",
            "zip_code": "54000",
            "password": "console2026",
            "accept_terms": True,
        },
    )
    _make_admin(engine)
    chats = c.get("/api/admin/conversations").json()
    assert chats[0]["blocked"] == 1 and chats[0]["customer"] == "ali@example.com"
    detail = c.get(f"/api/admin/conversations/{chats[0]['id']}").json()
    tools = [t for m in detail["messages"] for t in m["tools"]]
    assert [t["name"] for t in tools] == ["verify_customer", "cancel_order"]
    assert tools[1]["ok"] is False and tools[1]["result"].startswith("NOT ALLOWED")
    assert c.get("/api/admin/conversations/nope").status_code == 404


def test_admin_page_is_served_without_vendor_names(engine: Engine) -> None:
    c = _console(engine)
    r = c.get("/admin")
    assert r.status_code == 200 and "admin.js?v=" in r.text and "admin.css?v=" in r.text
    assert r.headers["x-robots-tag"] == "noindex"
    for word in ["ollama", "llama", "qwen"]:
        assert word not in r.text.lower()
        assert word not in c.get("/static/js/admin.js").text.lower()


def test_cli_make_admin_and_import(
    engine: Engine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    url = str(engine.url).replace("***", "")
    if not url.startswith("sqlite"):
        pytest.skip("CLI test uses the SQLite file database")
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("KAVRELLE_DATABASE_URL", url)
    _console(engine)
    runner = CliRunner()
    assert runner.invoke(cli, ["make-admin", "nobody@example.com"]).exit_code == 1
    assert runner.invoke(cli, ["make-admin", "owner@example.com"]).exit_code == 0
    with session_scope(make_session_factory(engine)) as s:
        assert (
            s.scalar(
                select(Customer.is_admin).where(Customer.email == "owner@example.com")
            )
            is True
        )
    out = runner.invoke(
        cli, ["import-run", str(_write_folder(tmp_path)), "--name", "first"]
    )
    assert out.exit_code == 0 and "12 metrics, 1 violations" in out.output
    assert runner.invoke(cli, ["import-run", str(tmp_path / "missing")]).exit_code == 1
    with session_scope(make_session_factory(engine)) as s:
        assert s.scalar(select(EvalRun.name)) == "first"


def test_lab_progress_survives_concurrent_polling(engine: Engine) -> None:
    c = _console(engine, _fake_evaluator(delay=0.3))
    _make_admin(engine)
    c.post("/api/admin/runs", json={"n_runs": 1})
    errors: list[int] = []

    def poll() -> None:
        for _ in range(20):
            code = c.get("/api/admin/lab/progress").status_code
            if code != 200:
                errors.append(code)

    threads = [threading.Thread(target=poll) for _ in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()
    _wait_done(c)
    assert errors == []
