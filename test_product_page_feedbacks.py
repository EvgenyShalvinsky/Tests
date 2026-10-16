"""Product page should expose nm_id for client-side feedbacks loader."""

from unittest.mock import AsyncMock, MagicMock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import get_db
from app.dependencies import get_current_user_optional
from app.routers import pages


def _make_app():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    pages.templates.env.loader.searchpath = [str(root / "app" / "templates")]

    app = FastAPI()
    app.include_router(pages.router)

    async def fake_db():
        yield MagicMock()

    async def fake_user():
        return None

    app.dependency_overrides[get_db] = fake_db
    app.dependency_overrides[get_current_user_optional] = fake_user
    return app


def _product(*, slug: str, wb_id: int | None, yandex_offer_id: str | None = None):
    item = MagicMock()
    item.id = 1
    item.name = "Test product"
    item.slug = slug
    item.brand = "NOTRE SECRET"
    item.description = "desc"
    item.wb_id = wb_id
    item.yandex_offer_id = yandex_offer_id
    item.ozon_id = None
    item.media = []
    item.attributes = []
    item.offers = []
    item.reviews = []
    return item


def test_product_page_includes_data_nm_id_from_wb_id():
    app = _make_app()
    item = _product(slug="wine-patches", wb_id=256877073)

    with patch(
        "app.routers.pages.catalog_service.get_product_detail",
        new_callable=AsyncMock,
        return_value=item,
    ), patch(
        "app.routers.pages.catalog_service.get_sorted_offers",
        return_value=[],
    ), patch(
        "app.routers.pages.catalog_service.average_rating",
        return_value=None,
    ):
        with TestClient(app) as client:
            response = client.get("/product/wine-patches")

    assert response.status_code == 200
    assert 'id="feedbacks-section"' in response.text
    assert 'data-nm-id="256877073"' in response.text
    assert 'id="feedbacks-list"' in response.text


def test_product_page_falls_back_to_marketplace_nm_id():
    app = _make_app()
    item = _product(slug="gold-patches", wb_id=None)

    with patch(
        "app.routers.pages.catalog_service.get_product_detail",
        new_callable=AsyncMock,
        return_value=item,
    ), patch(
        "app.routers.pages.catalog_service.get_sorted_offers",
        return_value=[],
    ), patch(
        "app.routers.pages.catalog_service.average_rating",
        return_value=None,
    ):
        with TestClient(app) as client:
            response = client.get("/product/gold-patches")

    assert response.status_code == 200
    assert 'data-nm-id="256870838"' in response.text


def test_product_page_includes_yandex_offer_id():
    app = _make_app()
    item = _product(slug="wine-patches", wb_id=None, yandex_offer_id="YM-SKU-77")

    with patch(
        "app.routers.pages.catalog_service.get_product_detail",
        new_callable=AsyncMock,
        return_value=item,
    ), patch(
        "app.routers.pages.catalog_service.get_sorted_offers",
        return_value=[],
    ), patch(
        "app.routers.pages.catalog_service.average_rating",
        return_value=None,
    ), patch(
        "app.routers.pages.nm_id_from_marketplace",
        return_value=None,
    ):
        with TestClient(app) as client:
            response = client.get("/product/wine-patches")

    assert response.status_code == 200
    assert 'data-ym-offer-id="YM-SKU-77"' in response.text
    assert 'id="feedbacks-list"' in response.text
