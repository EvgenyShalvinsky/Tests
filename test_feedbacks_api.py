from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.models.feedback import WbFeedback, WbFeedbackSync
from app.routers.api import products
from app.schemas.feedbacks import FeedbackMedia, FeedbackOut, FeedbacksPage
from app.services import feedbacks_service
from app.services.feedbacks_service import clear_feedbacks_cache


@pytest_asyncio.fixture
async def db_session(tmp_path: Path):
    db_path = tmp_path / "feedbacks-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()
    clear_feedbacks_cache()


@pytest.fixture
def client(tmp_path: Path):
    clear_feedbacks_cache()
    db_path = tmp_path / "api-feedbacks.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    async def override_get_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with session_factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    clear_feedbacks_cache()


def _sample_page() -> FeedbacksPage:
    return FeedbacksPage(
        items=[
            FeedbackOut(
                id="1",
                text="Супер",
                pros="Качество",
                cons=None,
                productValuation=5,
                createdDate="2024-06-01T12:00:00Z",
                userName="Ирина",
                answer="Спасибо!",
                media=[FeedbackMedia(type="photo", url="https://example.com/p.jpg")],
            )
        ],
        total=1,
        skip=0,
        take=20,
        has_more=False,
    )


def test_feedbacks_api_returns_items(client):
    with patch(
        "app.services.feedbacks_service.get_product_feedbacks",
        new_callable=AsyncMock,
        return_value=_sample_page(),
    ) as mocked:
        response = client.get("/api/products/256877073/feedbacks?take=20&order=dateDesc")

    assert response.status_code == 200
    data = response.json()
    assert data["total"] == 1
    assert data["items"][0]["userName"] == "Ирина"
    assert data["items"][0]["productValuation"] == 5
    assert data["items"][0]["media"][0]["type"] == "photo"
    mocked.assert_awaited_once()
    assert mocked.await_args.args[1] == 256877073


def test_feedbacks_api_passes_filters(client):
    with patch(
        "app.services.feedbacks_service.get_product_feedbacks",
        new_callable=AsyncMock,
        return_value=FeedbacksPage(items=[], total=0, skip=0, take=20, has_more=False),
    ) as mocked:
        response = client.get(
            "/api/products/111/feedbacks",
            params={
                "skip": 20,
                "take": 10,
                "order": "dateAsc",
                "hasPhoto": True,
                "hasVideo": False,
                "minRating": 4,
            },
        )

    assert response.status_code == 200
    kwargs = mocked.await_args.kwargs
    assert kwargs["skip"] == 20
    assert kwargs["take"] == 10
    assert kwargs["order"] == "dateAsc"
    assert kwargs["has_photo"] is True
    assert kwargs["min_rating"] == 4


@pytest.mark.asyncio
async def test_get_product_feedbacks_saves_to_db_and_serves_from_cache(db_session, monkeypatch):
    monkeypatch.setenv("WB_FEEDBACKS_CACHE_TTL_SECONDS", "10800")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_feedbacks_cache()

    seller_raw = [
        {
            "id": "wb-42",
            "text": "Патчи супер",
            "productValuation": 5,
            "createdDate": "2024-05-01T00:00:00Z",
            "userName": "Оля",
            "photoLinks": [{"fullSize": "https://cdn.example/1.jpg"}],
        }
    ]
    fetch_mock = AsyncMock(return_value=seller_raw)

    with patch(
        "app.services.feedbacks_service.fetch_seller_feedbacks",
        fetch_mock,
    ), patch(
        "app.services.feedbacks_service.fetch_public_feedbacks",
        new_callable=AsyncMock,
        return_value=[],
    ):
        page1 = await feedbacks_service.get_product_feedbacks(db_session, 256877073, take=10)
        page2 = await feedbacks_service.get_product_feedbacks(db_session, 256877073, take=10)

    assert page1.total == 1
    assert page1.items[0].id == "wb-42"
    assert page2.items[0].text == "Патчи супер"
    assert fetch_mock.await_count == 1

    sync = await db_session.get(WbFeedbackSync, 256877073)
    assert sync is not None
    assert sync.last_updated_at is not None
    rows = (await db_session.execute(select(WbFeedback).where(WbFeedback.nm_id == 256877073))).scalars().all()
    assert len(rows) == 1
    assert rows[0].wb_feedback_id == "wb-42"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_stale_cache_refreshes_after_ttl(db_session, monkeypatch):
    monkeypatch.setenv("WB_FEEDBACKS_CACHE_TTL_SECONDS", "10800")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_feedbacks_cache()

    db_session.add(
        WbFeedbackSync(nm_id=1, last_updated_at=datetime.utcnow() - timedelta(hours=4))
    )
    db_session.add(
        WbFeedback(
            nm_id=1,
            wb_feedback_id="old",
            text="старый",
            product_valuation=3,
            created_date="2024-01-01T00:00:00Z",
            user_name="A",
            media_json="[]",
        )
    )
    await db_session.flush()

    new_raw = [
        {
            "id": "new",
            "text": "новый",
            "productValuation": 5,
            "createdDate": "2024-08-01T00:00:00Z",
            "userName": "B",
        }
    ]
    with patch(
        "app.services.feedbacks_service.fetch_seller_feedbacks",
        new_callable=AsyncMock,
        return_value=new_raw,
    ), patch(
        "app.services.feedbacks_service.fetch_public_feedbacks",
        new_callable=AsyncMock,
        return_value=[],
    ):
        page = await feedbacks_service.get_product_feedbacks(db_session, 1, take=10)

    assert page.total == 1
    assert page.items[0].id == "new"
    assert page.items[0].text == "новый"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_fresh_cache_skips_wb_api(db_session, monkeypatch):
    monkeypatch.setenv("WB_FEEDBACKS_CACHE_TTL_SECONDS", "10800")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_feedbacks_cache()

    db_session.add(WbFeedbackSync(nm_id=7, last_updated_at=datetime.utcnow()))
    db_session.add(
        WbFeedback(
            nm_id=7,
            wb_feedback_id="cached",
            text="из БД",
            product_valuation=5,
            created_date="2024-01-01T00:00:00Z",
            user_name="Кэш",
            media_json='[{"type":"photo","url":"https://x/y.jpg"}]',
        )
    )
    await db_session.flush()

    with patch(
        "app.services.feedbacks_service.fetch_seller_feedbacks",
        new_callable=AsyncMock,
    ) as fetch_mock:
        page = await feedbacks_service.get_product_feedbacks(db_session, 7, take=10)

    fetch_mock.assert_not_awaited()
    assert page.total == 1
    assert page.items[0].text == "из БД"
    assert page.items[0].media[0].url == "https://x/y.jpg"
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_wb_error_serves_stale_db_data(db_session, monkeypatch):
    from app.config import get_settings
    from app.integrations.wildberries import WildberriesAPIError

    monkeypatch.setenv("WB_FEEDBACKS_CACHE_TTL_SECONDS", "10800")
    get_settings.cache_clear()
    clear_feedbacks_cache()

    db_session.add(
        WbFeedbackSync(nm_id=9, last_updated_at=datetime.utcnow() - timedelta(hours=5))
    )
    db_session.add(
        WbFeedback(
            nm_id=9,
            wb_feedback_id="stale",
            text="старый кэш",
            product_valuation=4,
            created_date="2024-01-01T00:00:00Z",
            user_name="С",
            media_json="[]",
        )
    )
    await db_session.flush()

    with patch(
        "app.services.feedbacks_service.fetch_seller_feedbacks",
        new_callable=AsyncMock,
        side_effect=WildberriesAPIError("rate limit", status_code=429),
    ), patch(
        "app.services.feedbacks_service.fetch_public_feedbacks",
        new_callable=AsyncMock,
        return_value=[],
    ):
        page = await feedbacks_service.get_product_feedbacks(db_session, 9, take=10)

    assert page.error is None
    assert page.total == 1
    assert page.items[0].text == "старый кэш"
    get_settings.cache_clear()
