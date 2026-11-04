from pathlib import Path

import pytest

from kavrelle.config import DEFAULT_DATABASE_URL, Settings, read_env_file


def test_defaults_without_env_or_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("KAVRELLE_DATABASE_URL", raising=False)
    s = Settings.load(tmp_path / "missing.env")
    assert s.database_url == DEFAULT_DATABASE_URL
    assert s.brand == "Kavrelle"
    assert s.assistant_name == "Kavrelle Support"


def test_env_file_is_read(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.delenv("KAVRELLE_DATABASE_URL", raising=False)
    f = tmp_path / ".env"
    f.write_text(
        '# comment\n\nKAVRELLE_DATABASE_URL="sqlite:///x.db"\nBROKEN LINE\n', "utf-8"
    )
    assert Settings.load(f).database_url == "sqlite:///x.db"


def test_environment_beats_file(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    f = tmp_path / ".env"
    f.write_text("KAVRELLE_DATABASE_URL=sqlite:///file.db\n", "utf-8")
    monkeypatch.setenv("KAVRELLE_DATABASE_URL", "sqlite:///env.db")
    assert Settings.load(f).database_url == "sqlite:///env.db"


def test_read_env_file_edge_cases(tmp_path: Path) -> None:
    f = tmp_path / ".env"
    f.write_text("A='single'\nB = spaced \n=novalue\nC=a=b\nD=\"\n", "utf-8")
    assert read_env_file(f) == {"A": "single", "B": "spaced", "C": "a=b", "D": '"'}
    assert read_env_file(tmp_path / "nope") == {}
