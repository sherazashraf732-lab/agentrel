"""Settings for the Kavrelle mall, read from the environment and an optional .env file."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

DEFAULT_DATABASE_URL = "sqlite:///kavrelle.db"


def read_env_file(path: Path) -> dict[str, str]:
    """Parse KEY=VALUE lines; ignore blanks, comments and malformed lines."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key, value = key.strip(), value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "'\"":
            value = value[1:-1]
        if key:
            values[key] = value
    return values


@dataclass(frozen=True)
class Settings:
    database_url: str = DEFAULT_DATABASE_URL
    brand: str = "Kavrelle"
    assistant_name: str = "Kavrelle Support"

    @classmethod
    def load(cls, env_file: Path | None = Path(".env")) -> Settings:
        """Environment variables win over the .env file; the .env file wins over defaults."""
        file_values = read_env_file(env_file) if env_file else {}
        url = (
            os.environ.get("KAVRELLE_DATABASE_URL")
            or file_values.get("KAVRELLE_DATABASE_URL")
            or DEFAULT_DATABASE_URL
        )
        return cls(database_url=url)
