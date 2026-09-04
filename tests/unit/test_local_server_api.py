import asyncio
from pathlib import Path

import httpx
import pytest

import trackio
from trackio.sqlite_storage import SQLiteStorage
from trackio.utils import get_db_path, media_dir


def test_get_run_configs_returns_config_per_run(temp_dir):
    project_dir = temp_dir / "run_config_per_run"

    run_a = trackio.init(
        dir=project_dir,
        name="run-a",
        config={"lr": 0.01, "model": "resnet"},
        group="exp-1",
    )
    run_a_id = run_a.id
    trackio.log(metrics={"loss": 0.1})
    trackio.finish()

    run_b = trackio.init(
        dir=project_dir,
        name="run-b",
        config={"lr": 0.02, "model": "vit"},
    )
    run_b_id = run_b.id
    trackio.log(metrics={"loss": 0.2})
    trackio.finish()

    app, url = trackio.show(dir=project_dir, block_thread=False, open_browser=False)

    try:
        response = httpx.post(
            f"{url.rstrip('/')}/api/get_run_configs", json={}, timeout=5
        )
        response.raise_for_status()
        configs = response.json()["data"]

        assert set(configs.keys()) == {run_a_id, run_b_id}
        assert configs[run_a_id]["lr"] == 0.01
        assert configs[run_a_id]["model"] == "resnet"
        assert configs[run_a_id]["_Group"] == "exp-1"
        assert configs[run_b_id]["lr"] == 0.02
        assert configs[run_b_id]["model"] == "vit"
    finally:
        app.close()


def test_local_dashboard_runs_api(temp_dir):
    project_dir = temp_dir / "test_local_client"
    run_name = "client-run"

    trackio.init(dir=project_dir, name=run_name)
    trackio.log(metrics={"loss": 0.1})
    trackio.finish()

    app, url = trackio.show(dir=project_dir, block_thread=False, open_browser=False)

    try:
        runs_response = httpx.post(
            f"{url.rstrip('/')}/api/get_runs_for_project", json={}, timeout=5
        )
        runs_response.raise_for_status()
        runs = runs_response.json()["data"]

        settings_response = httpx.post(
            f"{url.rstrip('/')}/api/get_settings", json={}, timeout=5
        )
        settings_response.raise_for_status()
        settings = settings_response.json()["data"]

        assert len(runs) == 1
        assert runs[0]["name"] == run_name
        assert "logo_urls" in settings
        assert settings["project_name"] == project_dir.name
    finally:
        app.close()


def test_local_dashboard_injects_live_reload_for_custom_frontend(temp_dir):
    frontend_dir = Path(temp_dir) / "custom-frontend"
    frontend_dir.mkdir()
    (frontend_dir / "index.html").write_text(
        "<!doctype html><html><body><h1>Custom</h1></body></html>"
    )

    app, url = trackio.show(
        dir=temp_dir / "proj",
        block_thread=False,
        open_browser=False,
        frontend_dir=frontend_dir,
    )

    try:
        response = httpx.get(url, timeout=5)
        version_response = httpx.get(
            f"{url.rstrip('/')}/__trackio/frontend_version",
            timeout=5,
        )

        assert response.status_code == 200
        assert "/__trackio/frontend_version" in response.text
        assert version_response.status_code == 200
        assert "version" in version_response.json()
    finally:
        app.close()


def test_local_dashboard_file_endpoint_only_serves_trackio_paths(
    temp_dir, image_ndarray
):
    project_dir = temp_dir / "test_local_file_endpoint"
    run_name = "file-run"

    trackio.init(dir=project_dir, name=run_name)
    trackio.log(metrics={"image": trackio.Image(image_ndarray, caption="allowed")})
    trackio.finish()

    logs = SQLiteStorage.get_logs(get_db_path(project_dir), run=run_name)
    rel_path = logs[0]["image"]["file_path"]
    allowed_path = media_dir(project_dir) / rel_path

    app, url = trackio.show(dir=project_dir, block_thread=False, open_browser=False)

    try:
        allowed_response = httpx.get(
            f"{url.rstrip('/')}/file",
            params={"path": str(allowed_path)},
            timeout=5,
        )
        blocked_response = httpx.get(
            f"{url.rstrip('/')}/file",
            params={"path": "/etc/hosts"},
            timeout=5,
        )
        blocked_db_response = httpx.get(
            f"{url.rstrip('/')}/file",
            params={"path": str(get_db_path(project_dir))},
            timeout=5,
        )

        assert allowed_response.status_code == 200
        assert blocked_response.status_code == 404
        assert blocked_db_response.status_code == 404
    finally:
        app.close()


def test_get_tab_availability_reflects_data(temp_dir):
    from trackio.server import build_api_registry
    from trackio.utils import files_dir

    project_dir = temp_dir / "ta_srv"
    db_path = get_db_path(project_dir)
    registry = build_api_registry(project_dir)
    get_tab_availability = registry["get_tab_availability"]

    empty = get_tab_availability()
    assert empty == {
        "metrics": False,
        "system": False,
        "traces": False,
        "media": False,
        "reports": False,
        "files": False,
    }

    SQLiteStorage.bulk_log(db_path=db_path, run="r1", metrics_list=[{"loss": 0.25}])
    SQLiteStorage.bulk_alert(
        db_path=db_path,
        run="r1",
        titles=["alert"],
        texts=[None],
        levels=["warn"],
        steps=[None],
    )
    target = files_dir(project_dir)
    target.mkdir(parents=True, exist_ok=True)
    (target / "note.txt").write_text("hi")

    result = get_tab_availability()
    assert result["metrics"] is True
    assert result["reports"] is True
    assert result["files"] is True
    assert result["media"] is False
    assert result["system"] is False
    assert result["traces"] is False


def test_local_dashboard_supports_mcp(temp_dir):
    pytest.importorskip("mcp")
    from mcp import ClientSession
    from mcp.client.streamable_http import streamable_http_client

    project_dir = temp_dir / "test_local_mcp"
    run_name = "mcp-run"

    trackio.init(dir=project_dir, name=run_name)
    trackio.log(metrics={"loss": 0.1})
    trackio.finish()

    app, url = trackio.show(
        dir=project_dir,
        block_thread=False,
        open_browser=False,
        mcp_server=True,
    )

    async def check_mcp() -> None:
        async with streamable_http_client(f"{url.rstrip('/')}/mcp") as (
            read_stream,
            write_stream,
            _,
        ):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                tools = await session.list_tools()
                tool_names = {tool.name for tool in tools.tools}
                assert "get_runs_for_project" in tool_names
                assert "get_run_summary" in tool_names

                runs = await session.call_tool("get_runs_for_project")
                result = runs.structuredContent["result"]
                assert len(result) == 1
                assert result[0]["name"] == run_name

                run_summary = await session.call_tool(
                    "get_run_summary",
                    {"run": run_name},
                )
                assert run_summary.structuredContent["run"] == run_name
                assert run_summary.structuredContent["num_logs"] == 1

    try:
        asyncio.run(check_mcp())
    finally:
        app.close()


def test_bundled_frontend_serves_rebuilt_outlier_filter(temp_dir):
    from trackio.frontend_config import BUNDLED_FRONTEND_DIR

    js_files = list((BUNDLED_FRONTEND_DIR / "assets").glob("index-*.js"))
    assert len(js_files) == 1
    js_name = js_files[0].name
    assert "Outlier filter" in js_files[0].read_text()
    assert "Top (head)" in js_files[0].read_text()

    app, url = trackio.show(
        dir=temp_dir / "proj",
        block_thread=False,
        open_browser=False,
    )
    try:
        js = httpx.get(f"{url.rstrip('/')}/assets/{js_name}", timeout=5)
        js.raise_for_status()
        assert "Outlier filter" in js.text
        assert "Top (head)" in js.text
        assert "locked-project" in js.text
    finally:
        app.close()


def test_show_reads_legacy_project_named_db(temp_dir):
    project_dir = temp_dir / "track"
    project_dir.mkdir()
    SQLiteStorage.init_db(project_dir / "trackio.db")
    SQLiteStorage.bulk_log(
        db_path=project_dir / "mel_ae.db",
        run="mel_ae",
        metrics_list=[{"train/recon_loss": 0.12}],
        steps=[3],
    )

    app, url = trackio.show(dir=project_dir, block_thread=False, open_browser=False)
    try:
        page = httpx.get(url, timeout=5)
        page.raise_for_status()
        assert "No projects" not in page.text

        projects_response = httpx.post(
            f"{url.rstrip('/')}/api/get_all_projects", json={}, timeout=5
        )
        projects_response.raise_for_status()
        assert projects_response.json()["data"] == ["mel_ae"]

        runs_response = httpx.post(
            f"{url.rstrip('/')}/api/get_runs_for_project",
            json={"project": "mel_ae"},
            timeout=5,
        )
        runs_response.raise_for_status()
        runs = runs_response.json()["data"]
        assert len(runs) == 1
        assert runs[0]["name"] == "mel_ae"
        run_id = runs[0]["id"]

        settings_response = httpx.post(
            f"{url.rstrip('/')}/api/get_settings", json={}, timeout=5
        )
        settings_response.raise_for_status()

        tabs_response = httpx.post(
            f"{url.rstrip('/')}/api/get_tab_availability",
            json={"project": "mel_ae"},
            timeout=5,
        )
        tabs_response.raise_for_status()
        assert tabs_response.json()["data"]["metrics"] is True

        metrics_response = httpx.post(
            f"{url.rstrip('/')}/api/get_metrics_for_run",
            json={"project": "mel_ae", "run": "mel_ae", "run_id": run_id},
            timeout=5,
        )
        metrics_response.raise_for_status()
        assert "train/recon_loss" in metrics_response.json()["data"]

        logs_response = httpx.post(
            f"{url.rstrip('/')}/api/get_logs_batch",
            json={
                "project": "mel_ae",
                "runs": [{"run": "mel_ae", "run_id": run_id}],
                "scalar_only": True,
            },
            timeout=5,
        )
        logs_response.raise_for_status()
        logs = logs_response.json()["data"]
        assert len(logs) == 1
        assert logs[0]["logs"][0]["train/recon_loss"] == 0.12
    finally:
        app.close()


def test_create_app_does_not_create_trackio_db_when_legacy_db_has_data(temp_dir):
    from trackio.server import build_api_registry, create_app

    project_dir = temp_dir / "track"
    project_dir.mkdir()
    SQLiteStorage.bulk_log(
        db_path=project_dir / "mel_ae.db",
        run="mel_ae",
        metrics_list=[{"loss": 1.0}],
        steps=[0],
    )

    create_app(project_dir)
    assert not (project_dir / "trackio.db").exists()
    registry = build_api_registry(project_dir)
    assert registry["get_all_projects"]() == ["mel_ae"]
    runs = registry["get_runs_for_project"]()
    assert len(runs) == 1
    assert runs[0]["name"] == "mel_ae"


def test_get_all_projects_canonical_db_uses_directory_name(temp_dir):
    from trackio.server import build_api_registry

    project_dir = temp_dir / "my_exp"
    trackio.init(dir=project_dir, name="run-a")
    trackio.log(metrics={"loss": 0.1})
    trackio.finish()
    assert build_api_registry(project_dir)["get_all_projects"]() == ["my_exp"]
