"""Run a private PostgreSQL for Kavrelle from your user folder: no admin rights, no service.

    python scripts/local_postgres.py setup    # once: create the cluster, roles, databases and .env
    python scripts/local_postgres.py start    # after a reboot
    python scripts/local_postgres.py stop
    python scripts/local_postgres.py status

It uses the PostgreSQL programs already installed (PG_BIN, default the PostgreSQL 18
folder). Passwords are generated, never printed, and saved only in files git ignores:
.env (the app's database URL) and agentrel-results/postgres-local-login.txt.
"""

from __future__ import annotations

import os
import secrets
import subprocess
import sys
import tempfile
import time
from pathlib import Path

PG_BIN = Path(os.environ.get("PG_BIN", r"C:\Program Files\PostgreSQL\18\bin"))
DATA = Path(os.environ.get("KAVRELLE_PGDATA", Path.home() / "pgdata" / "kavrelle"))
PORT = int(os.environ.get("KAVRELLE_PGPORT", "5433"))
REPO = Path(__file__).resolve().parent.parent
LOGIN = REPO / "agentrel-results" / "postgres-local-login.txt"
ENV = REPO / ".env"


def exe(name: str) -> str:
    path = PG_BIN / (name + (".exe" if os.name == "nt" else ""))
    if not path.exists():
        raise SystemExit(f"{path} not found. Set PG_BIN to your PostgreSQL bin folder.")
    return str(path)


def run(
    *args: str, env: dict[str, str] | None = None, check: bool = True
) -> subprocess.CompletedProcess[str]:
    print("  >", Path(args[0]).name, *(a for a in args[1:3]), flush=True)
    return subprocess.run(args, stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=120,
                          env={**os.environ, **(env or {})}, check=check)  # fmt: skip


def pg_ctl(action: str) -> str:
    args = [exe("pg_ctl"), action, "-D", str(DATA)]
    if action == "start":
        args += [
            "-l",
            str(DATA / "server.log"),
            "-o",
            f"-p {PORT} -c listen_addresses=localhost",
            "-w",
        ]
    if action == "start":  # the server outlives this script: don't hand it our pipes
        flags = (
            (subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP)
            if os.name == "nt"
            else 0
        )
        print("  > pg_ctl start", flush=True)
        subprocess.Popen(args, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                         creationflags=flags, close_fds=True)  # fmt: skip
        for _ in range(60):  # wait until the server answers
            time.sleep(1)
            if (
                run(
                    exe("pg_isready"), "-h", "localhost", "-p", str(PORT), check=False
                ).returncode
                == 0
            ):
                return "server started"
        code = 1
        return (
            "server started"
            if code == 0
            else f"start failed (code {code}), see {DATA / 'server.log'}"
        )
    r = run(*args, check=False)
    return (r.stdout + r.stderr).strip()


def psql(sql: str, password: str, db: str = "postgres") -> None:
    run(exe("psql"), "-h", "localhost", "-p", str(PORT), "-U", "postgres", "-d", db, "-v", "ON_ERROR_STOP=1",
        "-c", sql, env={"PGPASSWORD": password})  # fmt: skip


def setup() -> None:
    if (DATA / "PG_VERSION").exists():
        raise SystemExit(f"A cluster already exists in {DATA}. Use 'start'.")
    su_pw, app_pw = secrets.token_urlsafe(18), secrets.token_urlsafe(18)
    DATA.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", delete=False, suffix=".pw") as f:
        f.write(su_pw)
        pwfile = f.name
    try:
        run(exe("initdb"), "-D", str(DATA), "-U", "postgres", "--auth=scram-sha-256", f"--pwfile={pwfile}",
            "-E", "UTF8", "--locale=C")  # fmt: skip
    finally:
        os.unlink(pwfile)
    print(pg_ctl("start"))
    psql(f"CREATE ROLE kavrelle LOGIN PASSWORD '{app_pw}'", su_pw)
    psql("CREATE DATABASE kavrelle OWNER kavrelle", su_pw)
    psql("CREATE DATABASE kavrelle_test OWNER kavrelle", su_pw)
    url = f"postgresql+psycopg://kavrelle:{app_pw}@localhost:{PORT}/kavrelle"
    LOGIN.parent.mkdir(exist_ok=True)
    LOGIN.write_text(
        "Local PostgreSQL for Kavrelle (private: never commit or share)\n"
        f"Data folder: {DATA}\nPort: {PORT}\n\n"
        f"Superuser: postgres\nPassword: {su_pw}\n\n"
        f"App user: kavrelle\nPassword: {app_pw}\nDatabases: kavrelle, kavrelle_test\n\n"
        f"KAVRELLE_DATABASE_URL={url}\n"
        f"KAVRELLE_TEST_POSTGRES_URL={url}_test\n",
        "utf-8",
    )
    lines = [
        ln
        for ln in (ENV.read_text("utf-8").splitlines() if ENV.exists() else [])
        if not ln.startswith("KAVRELLE_DATABASE_URL=")
    ]
    ENV.write_text("\n".join([*lines, f"KAVRELLE_DATABASE_URL={url}"]) + "\n", "utf-8")
    print(f"Ready: databases kavrelle and kavrelle_test on localhost:{PORT}.")
    print(
        f"Logins saved in {LOGIN.relative_to(REPO)}; .env now points the app at PostgreSQL."
    )


def main() -> None:
    cmd = sys.argv[1] if len(sys.argv) > 1 else "status"
    if cmd == "setup":
        setup()
    elif cmd in ("start", "stop", "status"):
        print(pg_ctl(cmd))
        if cmd == "start":
            time.sleep(0.5)
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main()
