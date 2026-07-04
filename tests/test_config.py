from pathlib import Path

from typer.testing import CliRunner

from lychee_alphadesk.cli.app import app
from lychee_alphadesk.core.config import (
    config_file_path,
    ensure_config_file,
    load_config,
)

runner = CliRunner()


def test_config_file_path_uses_xdg_config_home(monkeypatch) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", "/tmp/lad-config-test")

    assert config_file_path() == Path("/tmp/lad-config-test/lychee-alphadesk/config.yaml")


def test_ensure_config_file_creates_private_yaml(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    path = ensure_config_file()

    assert path == tmp_path / "lychee-alphadesk" / "config.yaml"
    assert path.exists()
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    config = load_config(path)
    assert "alpha_vantage" in config.providers
    assert config.providers["alpha_vantage"].registration_url.startswith("https://")


def test_setup_command_shows_config_path_and_provider_urls(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup"])

    assert result.exit_code == 0
    assert str(tmp_path / "lychee-alphadesk" / "config.yaml") in result.stdout
    assert "lad setup providers" in result.stdout
    assert "Alpha Vantage" in result.stdout
    assert "https://www.alphavantage.co/support/#api-key" in result.stdout


def test_setup_set_writes_provider_secret(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "alpha_vantage", "demo-key"])

    assert result.exit_code == 0
    assert "Saved alpha_vantage" in result.stdout
    config = load_config(config_file_path())
    assert config.providers["alpha_vantage"].value == "demo-key"


def test_setup_set_rejects_unknown_provider(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))

    result = runner.invoke(app, ["setup", "set", "unknown_provider", "demo-key"])

    assert result.exit_code == 1
    assert "Unknown provider" in result.stdout
