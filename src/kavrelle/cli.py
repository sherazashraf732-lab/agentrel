"""Command line for the Kavrelle mall: `kavrelle setup`, `kavrelle check`, `kavrelle serve`."""

from __future__ import annotations

import typer

from kavrelle import migrate
from kavrelle.config import Settings
from kavrelle.db import make_engine, make_session_factory, ping, session_scope
from kavrelle.seed import seed

app = typer.Typer(help="Kavrelle 3D mall", no_args_is_help=True)


def _safe_url(url: str) -> str:
    """Hide the password when printing a database URL."""
    if "@" not in url or "://" not in url:
        return url
    scheme, rest = url.split("://", 1)
    creds, host = rest.rsplit("@", 1)
    user = creds.split(":", 1)[0]
    return f"{scheme}://{user}:***@{host}"


@app.command()
def setup() -> None:
    """Create or upgrade the database tables, then load the catalogue."""
    settings = Settings.load()
    typer.echo(f"Database: {_safe_url(settings.database_url)}")
    migrate.upgrade(settings.database_url)
    engine = make_engine(settings.database_url)
    with session_scope(make_session_factory(engine)) as session:
        result = seed(session)
    engine.dispose()
    typer.echo(f"Ready. Added {result.shops} shops, {result.products} products, "
               f"{result.customers} customers, {result.orders} orders.")  # fmt: skip


@app.command()
def check() -> None:
    """Exit with code 1 if the database cannot be reached or is not migrated."""
    settings = Settings.load()
    engine = make_engine(settings.database_url)
    if not ping(engine):
        typer.echo(f"Database is down: {_safe_url(settings.database_url)}", err=True)
        raise typer.Exit(1)
    current, head = migrate.current_revision(engine), migrate.head_revision()
    engine.dispose()
    if current != head:
        typer.echo(
            f"Database needs `kavrelle setup` (at {current}, latest {head}).", err=True
        )
        raise typer.Exit(1)
    typer.echo("Database OK and up to date.")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8000) -> None:
    """Start the Kavrelle API."""
    import uvicorn

    from kavrelle.api import create_app

    uvicorn.run(create_app(), host=host, port=port)


@app.command("make-admin")
def make_admin(email: str) -> None:
    """Give an existing account access to the admin console (/admin)."""
    from sqlalchemy import select

    from kavrelle.models import Customer

    settings = Settings.load()
    engine = make_engine(settings.database_url)
    with session_scope(make_session_factory(engine)) as session:
        c = session.scalar(
            select(Customer).where(Customer.email == email.strip().lower())
        )
        if c is None or not c.password_hash:
            typer.echo(
                "No account with that email. Create it on the website first.", err=True
            )
            raise typer.Exit(1)
        c.is_admin = True
    engine.dispose()
    typer.echo(f"{email} can now open /admin.")


@app.command("import-run")
def import_run(
    folder: str, name: str = "", label: str = "Support Assistant v1"
) -> None:
    """Load a finished `agentrel run` output folder into the Reliability Lab."""
    from pathlib import Path

    from kavrelle.lab import import_results_dir

    path = Path(folder)
    settings = Settings.load()
    engine = make_engine(settings.database_url)
    with session_scope(make_session_factory(engine)) as session:
        try:
            run = import_results_dir(session, path, name or path.name, label)
        except (OSError, ValueError) as e:
            typer.echo(f"Could not import: {e}", err=True)
            raise typer.Exit(1) from e
        typer.echo(
            f"Imported {run.name}: {len(run.metrics)} metrics, {len(run.violations)} violations."
        )
    engine.dispose()


if __name__ == "__main__":
    app()
