import pytest

from app.services.feedbacks_service import (
    _is_fresh,
    clear_feedbacks_cache,
    merge_raw_feedbacks,
    nm_id_from_marketplace,
    normalize_feedback,
)
from app.models.feedback import WbFeedbackSync
from datetime import datetime, timedelta


@pytest.fixture(autouse=True)
def _clear_cache():
    clear_feedbacks_cache()
    yield
    clear_feedbacks_cache()


def test_is_fresh_within_three_hours():
    sync = WbFeedbackSync(nm_id=1, last_updated_at=datetime.utcnow() - timedelta(hours=2, minutes=59))
    assert _is_fresh(sync, 3 * 60 * 60) is True
    stale = WbFeedbackSync(nm_id=1, last_updated_at=datetime.utcnow() - timedelta(hours=3, minutes=1))
    assert _is_fresh(stale, 3 * 60 * 60) is False
    assert _is_fresh(None, 3 * 60 * 60) is False


def test_normalize_feedback_maps_wb_fields():
    raw = {
        "id": "fb-1",
        "text": "Отличный товар",
        "pros": "Качество",
        "cons": "Нет",
        "productValuation": 5,
        "createdDate": "2024-01-15T10:00:00Z",
        "userName": "Анна",
        "answer": {"text": "Спасибо!"},
        "photoLinks": [{"fullSize": "https://example.com/photo.jpg"}],
        "video": {"link": "https://example.com/video.mp4"},
    }
    item = normalize_feedback(raw)
    assert item is not None
    assert item.id == "fb-1"
    assert item.text == "Отличный товар"
    assert item.pros == "Качество"
    assert item.cons is None  # "Нет" treated as empty
    assert item.productValuation == 5
    assert item.userName == "Анна"
    assert item.answer == "Спасибо!"
    assert len(item.media) == 2
    assert {m.type for m in item.media} == {"photo", "video"}


def test_normalize_feedback_skips_without_id():
    assert normalize_feedback({"text": "no id"}) is None


def test_merge_raw_feedbacks_deduplicates_by_id():
    a = [{"id": "1", "text": "first", "productValuation": 5, "createdDate": "2024-01-01"}]
    b = [
        {"id": "1", "text": "dup", "productValuation": 4, "createdDate": "2024-01-02"},
        {"id": "2", "text": "second", "productValuation": 3, "createdDate": "2024-01-03"},
    ]
    merged = merge_raw_feedbacks(a, b)
    assert len(merged) == 2
    by_id = {i.id: i for i in merged}
    assert by_id["1"].text == "first"
    assert by_id["2"].text == "second"


def test_nm_id_from_marketplace_direct_and_url():
    assert nm_id_from_marketplace({"nm_id": 256877073}) == 256877073
    assert nm_id_from_marketplace(
        {"wb": "https://www.wildberries.ru/catalog/256870838/detail.aspx"}
    ) == 256870838
    assert nm_id_from_marketplace(None) is None
    assert nm_id_from_marketplace({}) is None
