"""
Tests for Web UI static file hosting, root routing, and HTTP method compatibility.
Ensures the UI web page and all required assets load reliably in both local and container environments.
"""

from pathlib import Path

from fastapi.testclient import TestClient

from src.app import _resolve_web_dir, app


def test_root_serves_html():
    """GET / must return 200 OK with HTML content containing the trading suite interface."""
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")
    content = response.text
    assert "EMA + VWAP" in content
    assert "Trading Suite" in content
    assert "/static/style.css" in content
    assert "/static/app.js" in content


def test_root_supports_head_method():
    """HEAD / must return 200 OK (not 405 Method Not Allowed) for preflights and healthcheckers."""
    client = TestClient(app)
    response = client.head("/")
    assert response.status_code == 200
    assert "text/html" in response.headers.get("content-type", "")


def test_optuna_concurrency_settings_describe_thread_workers():
    client = TestClient(app)
    response = client.get("/")

    assert response.status_code == 200
    assert "Trial Concurrency" in response.text
    assert "Optuna uses threads" in response.text
    assert "Multiprocessing acceleration" not in response.text


def test_index_html_alias():
    """GET /index.html and HEAD /index.html must resolve to the SPA root dashboard."""
    client = TestClient(app)
    get_res = client.get("/index.html")
    assert get_res.status_code == 200
    assert "text/html" in get_res.headers.get("content-type", "")
    assert "EMA + VWAP" in get_res.text

    head_res = client.head("/index.html")
    assert head_res.status_code == 200
    assert "text/html" in head_res.headers.get("content-type", "")


def test_static_css_served():
    """GET /static/style.css must return 200 with text/css content type."""
    client = TestClient(app)
    response = client.get("/static/style.css")
    assert response.status_code == 200
    assert "text/css" in response.headers.get("content-type", "")
    assert len(response.text) > 0


def test_static_javascript_assets_served():
    """Verify all core client-side JavaScript assets are accessible."""
    client = TestClient(app)
    for js_file in ["app.js", "tear_sheet.js", "ws_dashboard.js"]:
        response = client.get(f"/static/{js_file}")
        assert response.status_code == 200, f"Failed to fetch /static/{js_file}"
        assert "javascript" in response.headers.get("content-type", "")
        assert len(response.text) > 0


def test_optuna_ui_labels_in_sample_and_requires_matching_validation():
    client = TestClient(app)
    response = client.get("/static/app.js")

    assert response.status_code == 200
    assert "IN-SAMPLE ONLY" in response.text
    assert (
        "Run out-of-sample robustness validation before deploying Optuna parameters."
        in response.text
    )
    assert (
        "window._lastOptunaValidatedParams !== JSON.stringify(targetParams)"
        in response.text
    )


def test_favicon_served():
    """GET /favicon.ico must return 204 No Content or 200 OK."""
    client = TestClient(app)
    response = client.get("/favicon.ico")
    assert response.status_code in (200, 204)


def test_web_dir_resolver_finds_valid_directory():
    """_resolve_web_dir must discover a directory containing index.html."""
    resolved = _resolve_web_dir()
    assert isinstance(resolved, Path)
    assert resolved.is_dir()
    assert (resolved / "index.html").is_file()


def test_fallback_when_index_file_missing(monkeypatch, tmp_path):
    """When index.html does not exist, GET / must return 200 JSON with helpful status message."""
    monkeypatch.setattr("src.app._resolve_web_dir", lambda: tmp_path)
    client = TestClient(app)
    response = client.get("/")
    assert response.status_code == 200
    data = response.json()
    assert "message" in data
    assert "Static UI loading" in data["message"]
