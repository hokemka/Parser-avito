import pytest

from tgbot.services.ai import AiError, Evaluation, SearchRequest, extract_answer, heuristic_evaluation, parse_evaluation
from tgbot.services.avito import Listing


def test_extract_answer_legacy_shape():
    assert extract_answer({"aiRecord": {"aiRecordDetail": {"resultObject": ["  {\"rating\": 7}"]}}}).strip() == '{"rating": 7}'


def test_extract_answer_unified_shape():
    assert extract_answer({"data": {"content": "hello"}}) == "hello"


def test_extract_answer_empty_raises():
    with pytest.raises(AiError):
        extract_answer({"aiRecord": {}})


def test_parse_evaluation_with_fences_and_strings():
    raw = '```json\n{"rating": "8.4", "verdict": "BUY", "condition": "хорошее", "summary": "ok", "pros": "один плюс", "market_price": "42 000", "red_flags": []}\n```'
    ev = parse_evaluation(raw)
    assert ev.rating == 8.4 and ev.verdict == "buy" and ev.market_price == 42000
    assert ev.pros == ["один плюс"]


def test_parse_evaluation_clamps_and_derives_verdict():
    ev = parse_evaluation('{"rating": 14, "condition": "x"}')
    assert ev.rating == 10 and ev.verdict == "buy"
    ev = parse_evaluation('{"rating": 2.2, "verdict": "weird"}')
    assert ev.verdict == "skip"


def test_parse_evaluation_rejects_garbage():
    with pytest.raises(AiError):
        parse_evaluation("no json here")


def test_evaluation_json_roundtrip():
    ev = Evaluation(rating=7.0, verdict="consider", matches_request=True, condition="ok", condition_score=6.0, summary="s")
    assert Evaluation.from_json(ev.to_json()) == ev


def test_heuristic_evaluation_prefers_matching_cheap_listing():
    request = SearchRequest("iphone 13 128", "Москва", 30000, 45000)
    good = Listing(id=1, title="iPhone 13 128gb", price=35000, url="u", images=["i"], description="Отличный")
    bad = Listing(id=2, title="Чехол для iphone", price=500, url="u")
    assert heuristic_evaluation(request, good).rating > heuristic_evaluation(request, bad).rating
    assert heuristic_evaluation(request, good).ai_used is False


def test_fingerprint_stable():
    a = SearchRequest("IPhone 13 ", "Москва", 1, 2, "x")
    b = SearchRequest("iphone 13", "Казань", 1, 2, "X")
    assert a.fingerprint == b.fingerprint
