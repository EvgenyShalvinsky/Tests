from unittest.mock import AsyncMock, patch

import httpx
import pytest

from app.config import get_settings
from app.integrations.wildberries import WildberriesAPIError, fetch_seller_feedbacks


@pytest.fixture(autouse=True)
def _reload_settings(monkeypatch):
    monkeypatch.setenv("WB_API_TOKEN", "test-token")
    get_settings.cache_clear()
    yield
    get_settings.cache_clear()


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict | None = None, text: str = "", headers: dict | None = None):
        self.status_code = status_code
        self._payload = payload or {}
        self.text = text or str(payload)
        self.headers = headers or {}

    def json(self):
        return self._payload


@pytest.mark.asyncio
async def test_fetch_seller_feedbacks_merges_answered_and_unanswered():
    calls: list[dict] = []

    async def fake_get(url, params=None, headers=None):
        calls.append({"url": url, "params": dict(params or {})})
        answered = params.get("isAnswered")
        if answered is None:
            return _FakeResponse(200, {"data": {"feedbacks": [{"id": "a1", "text": "arch"}]}})
        if answered == "false":
            return _FakeResponse(200, {"data": {"feedbacks": [{"id": "u1", "text": "new"}]}})
        return _FakeResponse(200, {"data": {"feedbacks": [{"id": "u1", "text": "dup"}, {"id": "a2", "text": "old"}]}})

    mock_client = AsyncMock()
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.integrations.wildberries.httpx.AsyncClient", return_value=mock_client):
        with patch("app.integrations.wildberries.asyncio.sleep", new_callable=AsyncMock):
            items = await fetch_seller_feedbacks(256877073, take=100)

    ids = {i["id"] for i in items}
    assert ids == {"u1", "a2", "a1"}
    assert any(c["params"].get("nmId") == 256877073 for c in calls)
    assert any(c["params"].get("isAnswered") == "false" for c in calls)
    assert any(c["params"].get("isAnswered") == "true" for c in calls)
    assert all(c["url"].endswith("/api/v1/feedbacks/archive") for c in calls)


@pytest.mark.asyncio
async def test_fetch_seller_feedbacks_keeps_partial_on_one_failure():
    async def fake_get(url, params=None, headers=None):
        if params.get("isAnswered") == "false":
            return _FakeResponse(200, {"data": {"feedbacks": [{"id": "ok", "text": "yes"}]}})
        if params.get("isAnswered") == "true":
            raise WildberriesAPIError("archive fail", status_code=403)
        return _FakeResponse(429, text="rate limit")

    mock_client = AsyncMock()
    mock_client.get = fake_get
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.integrations.wildberries.httpx.AsyncClient", return_value=mock_client):
        with patch("app.integrations.wildberries.asyncio.sleep", new_callable=AsyncMock):
            items = await fetch_seller_feedbacks(1)

    assert len(items) == 1
    assert items[0]["id"] == "ok"


@pytest.mark.asyncio
async def test_fetch_seller_feedbacks_empty_without_token(monkeypatch):
    monkeypatch.setenv("WB_API_TOKEN", "")
    get_settings.cache_clear()
    items = await fetch_seller_feedbacks(1)
    assert items == []
