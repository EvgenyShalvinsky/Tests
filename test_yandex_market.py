from unittest.mock import AsyncMock, patch

import pytest

from app.integrations.yandex_market import (
    clear_business_id_cache,
    fetch_business_id,
    fetch_goods_feedbacks,
)


class _FakeResponse:
    def __init__(self, status_code: int, payload: dict):
        self.status_code = status_code
        self._payload = payload
        self.text = str(payload)
        self.headers = {}

    def json(self):
        return self._payload


@pytest.fixture(autouse=True)
def _token(monkeypatch):
    monkeypatch.setenv("YANDEX_MARKET_API_TOKEN", "test-ym-token")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_business_id_cache()
    yield
    clear_business_id_cache()
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_fetch_business_id_from_campaigns():
    async def fake_get(url, headers=None, params=None):
        assert url.endswith("/v2/campaigns")
        assert headers["Api-Key"] == "test-ym-token"
        return _FakeResponse(
            200,
            {"campaigns": [{"id": 10, "business": {"id": 999, "name": "Shop"}}]},
        )

    mock_client = AsyncMock()
    mock_client.get = fake_get

    business_id = await fetch_business_id(mock_client)
    assert business_id == 999


@pytest.mark.asyncio
async def test_fetch_goods_feedbacks_filters_by_offer_id():
    calls = {"n": 0}

    async def fake_get(url, headers=None, params=None):
        return _FakeResponse(
            200,
            {"campaigns": [{"business": {"id": 55}}]},
        )

    async def fake_post(url, headers=None, params=None, json=None):
        calls["n"] += 1
        assert "/v2/businesses/55/goods-feedback" in url
        assert json == {"offerIds": ["SKU-1"]}
        return _FakeResponse(
            200,
            {
                "result": {
                    "feedbacks": [
                        {
                            "feedbackId": 1,
                            "identifiers": {"offerId": "SKU-1"},
                            "description": {"comment": "ok"},
                            "statistics": {"rating": 5},
                        },
                        {
                            "feedbackId": 2,
                            "identifiers": {"offerId": "OTHER"},
                            "description": {"comment": "skip"},
                            "statistics": {"rating": 1},
                        },
                    ],
                    "paging": {},
                }
            },
        )

    mock_client = AsyncMock()
    mock_client.get = fake_get
    mock_client.post = fake_post
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("app.integrations.yandex_market.httpx.AsyncClient", return_value=mock_client):
        items = await fetch_goods_feedbacks("SKU-1")

    assert len(items) == 1
    assert items[0]["feedbackId"] == 1
    assert calls["n"] == 1
