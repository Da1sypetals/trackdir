from trackio import utils
from trackio.run import Run
from trackio.server import build_api_registry


def test_get_settings_default_values(monkeypatch, temp_dir):
    monkeypatch.delenv("TRACKIO_LOGO_LIGHT_URL", raising=False)
    monkeypatch.delenv("TRACKIO_LOGO_DARK_URL", raising=False)
    monkeypatch.delenv("TRACKIO_COLOR_PALETTE", raising=False)
    monkeypatch.delenv("TRACKIO_PLOT_ORDER", raising=False)
    monkeypatch.delenv("TRACKIO_TABLE_TRUNCATE_LENGTH", raising=False)

    result = build_api_registry(temp_dir)["get_settings"]()

    assert "light" in result["logo_urls"]
    assert "dark" in result["logo_urls"]
    assert result["color_palette"] == utils.DEFAULT_COLOR_PALETTE
    assert result["plot_order"] == []
    assert result["table_truncate_length"] == 250
    assert result["project_dir"] == str(temp_dir.resolve())


def test_custom_logos(monkeypatch):
    monkeypatch.setenv("TRACKIO_LOGO_LIGHT_URL", "https://example.com/light.png")
    monkeypatch.setenv("TRACKIO_LOGO_DARK_URL", "https://example.com/dark.png")
    result = utils.get_logo_urls()
    assert result["light"] == "https://example.com/light.png"
    assert result["dark"] == "https://example.com/dark.png"


def test_webhook_url_from_env(monkeypatch, temp_dir):
    monkeypatch.setenv("TRACKIO_WEBHOOK_URL", "https://hooks.slack.com/test")
    monkeypatch.delenv("TRACKIO_WEBHOOK_MIN_LEVEL", raising=False)
    run = Run(
        project_dir=temp_dir,
        name="test-run",
    )
    assert run._webhook_url == "https://hooks.slack.com/test"
    run.finish()
