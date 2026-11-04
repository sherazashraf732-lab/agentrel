"""Command line: `agentrel run config.yaml`."""

from __future__ import annotations

from pathlib import Path
from typing import Annotated

import typer

from agentrel.pipeline import evaluate, load_config, save_results
from agentrel.report import format_report

app = typer.Typer(help="Measure the reliability of an AI agent.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """agentrel: consistency, predictability and robustness for AI agents."""


@app.command()
def run(
    config_path: Annotated[Path, typer.Argument(exists=True, dir_okay=False)],
    min_overall: Annotated[
        float | None,
        typer.Option(
            help="Exit with code 1 if overall reliability is below this (for CI)."
        ),
    ] = None,
) -> None:
    """Run the agent described in CONFIG_PATH and print a reliability report."""
    config = load_config(config_path)
    report, runs = evaluate(config)
    out = Path(config.output_dir)
    save_results(report, runs, out)
    typer.echo(format_report(report))
    typer.echo(f"\nSaved runs and report.json to {out.resolve()}")

    if min_overall is not None and (
        report.overall is None or report.overall < min_overall
    ):
        typer.echo(f"FAIL: overall reliability below {min_overall}", err=True)
        raise typer.Exit(code=1)


@app.command()
def serve(
    host: str = "127.0.0.1",
    port: int = 8000,
    results_dir: Path = Path("agentrel-results"),
) -> None:
    """Start the website: TechMart store chat and reliability dashboard."""
    import uvicorn

    from agentrel.web.app import create_app

    typer.echo(
        f"Open http://{host}:{port}  (dashboard: http://{host}:{port}/dashboard)"
    )
    uvicorn.run(create_app(results_dir=results_dir), host=host, port=port)
