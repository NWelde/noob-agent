from typer.testing import CliRunner

from noob_agent.cli import app


def test_cli_displays_project_name() -> None:
    result = CliRunner().invoke(app, ["--help"])

    assert result.exit_code == 0
    assert "noob-agent" in result.output
