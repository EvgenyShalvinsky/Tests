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
from app.models.feedback import YmFeedback, YmFeedbackSync
from app.routers.api import products
from app.services import ym_feedbacks_service
from app.services.ym_feedbacks_service import clear_ym_feedbacks_cache, normalize_ym_feedback


@pytest_asyncio.fixture
async def db_session(tmp_path: Path):
    db_path = tmp_path / "ym-feedbacks-test.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()
    clear_ym_feedbacks_cache()


def test_normalize_ym_feedback():
    raw = {
        "feedbackId": 42,
        "createdAt": "2024-06-01T12:00:00Z",
        "author": "Игорь",
        "identifiers": {"offerId": "SKU-1"},
        "description": {
            "comment": "Хороший товар",
            "advantages": "Качество",
            "disadvantages": "Цена",
        },
        "media": {"photos": ["https://img/1.jpg"], "videos": []},
        "statistics": {"rating": 5},
    }
    item = normalize_ym_feedback(raw)
    assert item is not None
    assert item.id == "42"
    assert item.text == "Хороший товар"
    assert item.pros == "Качество"
    assert item.cons == "Цена"
    assert item.productValuation == 5
    assert item.media[0].type == "photo"


@pytest.mark.asyncio
async def test_ym_saves_new_and_skips_duplicates(db_session, monkeypatch):
    monkeypatch.setenv("YM_FEEDBACKS_CACHE_TTL_SECONDS", "10800")
    from app.config import get_settings

    get_settings.cache_clear()
    clear_ym_feedbacks_cache()

    raw = [
        {
            "feedbackId": 1,
            "createdAt": "2024-01-01T00:00:00Z",
            "author": "A",
            "identifiers": {"offerId": "OFFER-1"},
            "description": {"comment": "первый"},
            "media": {},
            "statistics": {"rating": 5},
        },
        {
            "feedbackId": 2,
            "createdAt": "2024-02-01T00:00:00Z",
            "author": "B",
            "identifiers": {"offerId": "OFFER-1"},
            "description": {"comment": "второй"},
            "media": {},
            "statistics": {"rating": 4},
        },
    ]
    fetch_mock = AsyncMock(return_value=raw)

    with patch("app.services.ym_feedbacks_service.fetch_goods_feedbacks", fetch_mock):
        page1 = await ym_feedbacks_service.get_ym_product_feedbacks(db_session, "OFFER-1", take=20)
        page2 = await ym_feedbacks_service.get_ym_product_feedbacks(db_session, "OFFER-1", take=20)

    assert page1.total == 2
    assert page2.total == 2
    assert fetch_mock.await_count == 1  # second call uses fresh DB cache

    rows = (await db_session.execute(select(YmFeedback))).scalars().all()
    assert len(rows) == 2

    # Force stale sync and return same + one new feedback — only new is inserted
    sync = await db_session.get(YmFeedbackSync, "OFFER-1")
    sync.last_updated_at = datetime.utcnow() - timedelta(hours=4)
    await db_session.flush()

    raw.append(
        {
            "feedbackId": 3,
            "createdAt": "2024-03-01T00:00:00Z",
            "author": "C",
            "identifiers": {"offerId": "OFFER-1"},
            "description": {"comment": "третий"},
            "media": {},
            "statistics": {"rating": 5},
        }
    )
    fetch_mock.return_value = raw

    with patch("app.services.ym_feedbacks_service.fetch_goods_feedbacks", fetch_mock):
        page3 = await ym_feedbacks_service.get_ym_product_feedbacks(db_session, "OFFER-1", take=20)

    assert page3.total == 3
    rows = (await db_session.execute(select(YmFeedback))).scalars().all()
    assert len(rows) == 3
    assert {r.feedback_id for r in rows} == {"1", "2", "3"}
    get_settings.cache_clear()


@pytest.mark.asyncio
async def test_ym_api_endpoint(tmp_path: Path):
    clear_ym_feedbacks_cache()
    db_path = tmp_path / "ym-api.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)

    async def override_get_db():
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            try:
                yield session
                await session.commit()
            except Exception:
                await session.rollback()
                raise

    app = FastAPI()
    app.include_router(products.router)
    app.dependency_overrides[get_db] = override_get_db

    sample = __import__("app.schemas.feedbacks", fromlist=["FeedbacksPage"]).FeedbacksPage(
        items=[], total=0, skip=0, take=20, has_more=False
    )

    with patch(
        "app.services.ym_feedbacks_service.get_ym_product_feedbacks",
        new_callable=AsyncMock,
        return_value=sample,
    ) as mocked:
        with TestClient(app) as client:
            response = client.get("/api/products/ym/SKU-123/feedbacks")

    assert response.status_code == 200
    assert mocked.await_args.args[1] == "SKU-123"
    clear_ym_feedbacks_cache()
