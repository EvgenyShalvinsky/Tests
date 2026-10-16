from datetime import datetime
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import pytest_asyncio
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from app.database import Base, get_db
from app.dependencies import get_current_user
from app.models.order import Order, OrderItem, OrderStatus, SubOrder, SubOrderStatus
from app.models.product import CatalogItem, Category, ProductOffer, ProductStatus
from app.models.seller import LegalType, Seller, SellerStatus
from app.models.user import User, UserRole
from app.routers.api import account
from app.services.auth_service import hash_password


@pytest_asyncio.fixture
async def db_session(tmp_path: Path):
    db_path = tmp_path / "account-orders.db"
    engine = create_async_engine(f"sqlite+aiosqlite:///{db_path}", echo=False)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with factory() as session:
        yield session
        await session.rollback()
    await engine.dispose()


@pytest.mark.asyncio
async def test_get_user_orders_loads_catalog_item_names(db_session):
    from app.services import order_service

    user = User(
        email="buyer@test.ru",
        password_hash=hash_password("pass12345"),
        full_name="Buyer",
        role=UserRole.customer,
    )
    seller_user = User(
        email="seller2@test.ru",
        password_hash=hash_password("pass12345"),
        full_name="Seller",
        role=UserRole.seller,
    )
    db_session.add_all([user, seller_user])
    await db_session.flush()

    seller = Seller(
        user_id=seller_user.id,
        company_name="Test Seller",
        legal_type=LegalType.llc,
        inn="123",
        status=SellerStatus.approved,
    )
    db_session.add(seller)
    await db_session.flush()

    category = Category(name="Cat", slug="cat-test")
    db_session.add(category)
    await db_session.flush()

    item = CatalogItem(
        category_id=category.id,
        name="Патчи Wine",
        slug="wine-test-order",
        brand="NS",
        status=ProductStatus.active,
    )
    db_session.add(item)
    await db_session.flush()

    offer = ProductOffer(
        catalog_item_id=item.id,
        seller_id=seller.id,
        sku="SKU-1",
        price=990,
        quantity=10,
    )
    db_session.add(offer)
    await db_session.flush()

    order = Order(
        user_id=user.id,
        status=OrderStatus.paid,
        total_amount=990,
        delivery_address="Москва",
        created_at=datetime.utcnow(),
    )
    db_session.add(order)
    await db_session.flush()

    sub = SubOrder(
        order_id=order.id,
        seller_id=seller.id,
        status=SubOrderStatus.delivered,
        delivery_cost=0,
    )
    db_session.add(sub)
    await db_session.flush()

    db_session.add(OrderItem(sub_order_id=sub.id, offer_id=offer.id, quantity=1, price=990))
    await db_session.flush()

    orders = await order_service.get_user_orders(db_session, user.id)
    assert len(orders) == 1
    loaded_item = orders[0].sub_orders[0].items[0]
    assert loaded_item.offer.catalog_item.name == "Патчи Wine"


def test_account_orders_api_returns_payload():
    app = FastAPI()
    app.include_router(account.router)

    fake_user = MagicMock()
    fake_user.id = 7

    async def override_user():
        return fake_user

    async def override_db():
        yield MagicMock()

    app.dependency_overrides[get_current_user] = override_user
    app.dependency_overrides[get_db] = override_db

    catalog_item = MagicMock()
    catalog_item.name = "Товар"

    offer = MagicMock()
    offer.catalog_item = catalog_item
    offer.sku = "SKU"

    oi = MagicMock()
    oi.quantity = 2
    oi.price = 750
    oi.offer = offer

    sub = MagicMock()
    sub.id = 1
    sub.seller = MagicMock(company_name="Seller Co")
    sub.status = SubOrderStatus.shipped
    sub.tracking_url = None
    sub.items = [oi]

    order = MagicMock()
    order.id = 11
    order.status = OrderStatus.paid
    order.total_amount = 1500
    order.created_at = datetime(2024, 5, 1, 12, 0, 0)
    order.delivery_address = "Адрес"
    order.sub_orders = [sub]

    with patch(
        "app.services.order_service.get_user_orders",
        new_callable=AsyncMock,
        return_value=[order],
    ):
        with TestClient(app) as client:
            response = client.get("/api/account/orders")

    assert response.status_code == 200
    data = response.json()
    assert data[0]["id"] == 11
    assert data[0]["status_label"] == "Оплачен"
    assert data[0]["sub_orders"][0]["items"][0]["name"] == "Товар"
    assert data[0]["total"] == 1500.0
