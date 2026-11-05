"""Publish Kavrelle to a free Hugging Face Space (Docker), with its database in Neon.

Before the first run:
  1. Sign in once:  hf auth login   (paste a token with "write" access; it stays on this PC)
  2. Put the hosted database address in .env.hosted (git ignores it):
       KAVRELLE_DATABASE_URL=postgresql+psycopg://USER:PASSWORD@HOST/DB?sslmode=require

Then:  uv run --with huggingface_hub python scripts/deploy_hf.py <hf-username>/kavrelle
The database address is stored as a private Space secret, never in the uploaded files.
"""

from __future__ import annotations

import shutil
import sys
import tempfile
from pathlib import Path

from huggingface_hub import HfApi

REPO = Path(__file__).resolve().parent.parent
HOSTED_ENV = REPO / ".env.hosted"


def hosted_settings() -> dict[str, str]:
    if not HOSTED_ENV.exists():
        return {}
    pairs = (
        line.split("=", 1)
        for line in HOSTED_ENV.read_text("utf-8").splitlines()
        if "=" in line and not line.startswith("#")
    )
    return {k.strip(): v.strip() for k, v in pairs}


def bundle(dest: Path) -> None:
    for name in ("Dockerfile", "start.sh", "README.md"):
        shutil.copy2(REPO / "deploy" / "huggingface" / name, dest / name)
    shutil.copy2(REPO / "pyproject.toml", dest / "pyproject.toml")
    shutil.copytree(
        REPO / "src",
        dest / "src",
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc"),
    )


def main() -> None:
    if len(sys.argv) != 2 or "/" not in sys.argv[1]:
        raise SystemExit(__doc__)
    space = sys.argv[1]
    api = HfApi()
    who = api.whoami()["name"]
    print(f"Signed in to Hugging Face as {who}.")
    api.create_repo(space, repo_type="space", space_sdk="docker", exist_ok=True)
    settings = hosted_settings()
    url = settings.get("KAVRELLE_DATABASE_URL", "")
    for plain in (
        "postgresql://",
        "postgres://",
    ):  # Neon gives the plain form; the app needs the driver
        if url.startswith(plain):
            url = "postgresql+psycopg://" + url[len(plain) :]
    if url:
        api.add_space_secret(space, "KAVRELLE_DATABASE_URL", url)
        print("Database address saved as a private Space secret.")
    else:
        print(
            "No .env.hosted found: the Space will use a temporary database that resets on restart."
        )
    with tempfile.TemporaryDirectory() as tmp:
        bundle(Path(tmp))
        api.upload_folder(folder_path=tmp, repo_id=space, repo_type="space", commit_message="Deploy Kavrelle",
                          delete_patterns=["src/**"])  # fmt: skip
    host = space.replace("/", "-").replace("_", "-").lower()
    print(
        f"Uploaded. The Space builds for ~10 minutes (it downloads the model), then opens at:\n"
        f"  https://{host}.hf.space\n  (build log: https://huggingface.co/spaces/{space})"
    )


if __name__ == "__main__":
    main()
