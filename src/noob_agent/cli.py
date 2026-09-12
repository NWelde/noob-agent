"""Command-line entry point for noob-agent."""

import typer

app = typer.Typer(help="noob-agent command-line interface.")


@app.callback()
def main() -> None:
    """Run noob-agent commands."""
