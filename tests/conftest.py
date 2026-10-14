import os
import sys
import pytest
from src.app import app
from src.auth import get_api_key


from unittest.mock import MagicMock

os.environ["API_KEY"] = "test-key-for-tests"

# Mock WeasyPrint before any test modules import pdf_renderer (required on Windows)
weasyprint_mock = MagicMock()
weasyprint_mock.HTML = MagicMock()
weasyprint_mock.text = MagicMock()
weasyprint_mock.text.fonts = MagicMock()
weasyprint_mock.text.fonts.FontConfiguration = MagicMock()
weasyprint_mock.css = MagicMock()

sys.modules["weasyprint"] = weasyprint_mock
sys.modules["weasyprint.text"] = weasyprint_mock.text
sys.modules["weasyprint.text.fonts"] = weasyprint_mock.text.fonts
sys.modules["weasyprint.css"] = weasyprint_mock.css


@pytest.fixture(autouse=True)
def mock_auth(monkeypatch):
    async def fake_api_key(api_key_header: str | None = None):
        return "test-key-for-tests"

    # Only override via FastAPI dependency injection; do not monkeypatch source
    # so that popping overrides in tests restores real authentication behavior.
    app.dependency_overrides[get_api_key] = fake_api_key

    yield

    app.dependency_overrides.pop(get_api_key, None)
