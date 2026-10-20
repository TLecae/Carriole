from etl_engine import __version__
from etl_engine.cli import app
from typer.testing import CliRunner


def test_version():
    result = CliRunner().invoke(app, ["--version"])
    assert result.exit_code == 0
    assert result.stdout.strip() == __version__
