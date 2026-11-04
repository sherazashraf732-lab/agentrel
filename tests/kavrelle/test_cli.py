from pathlib import Path

import pytest
from typer.testing import CliRunner

from kavrelle.cli import _safe_url, app

runner = CliRunner()


def test_setup_then_check(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "KAVRELLE_DATABASE_URL", f"sqlite:///{(tmp_path / 'k.db').as_posix()}"
    )
    before = runner.invoke(app, ["check"])
    assert before.exit_code == 1
    first = runner.invoke(app, ["setup"])
    assert first.exit_code == 0, first.output
    assert "Added 12 shops" in first.output
    second = runner.invoke(app, ["setup"])
    assert "Added 0 shops" in second.output
    assert runner.invoke(app, ["check"]).exit_code == 0


def test_check_fails_when_database_is_down(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv(
        "KAVRELLE_DATABASE_URL",
        f"sqlite:///{(tmp_path / 'x' / 'y' / 'z.db').as_posix()}",
    )
    assert runner.invoke(app, ["check"]).exit_code == 1


@pytest.mark.parametrize(
    ("url", "shown"),
    [
        ("postgresql+psycopg://kav:s3cret@localhost:5432/kavrelle",
         "postgresql+psycopg://kav:***@localhost:5432/kavrelle"),
        ("sqlite:///kavrelle.db", "sqlite:///kavrelle.db"),
        ("postgresql://kav:p@ss@host/db", "postgresql://kav:***@host/db"),
    ],
)  # fmt: skip
def test_passwords_are_never_printed(url: str, shown: str) -> None:
    assert _safe_url(url) == shown
