import json
from pathlib import Path

import pandas as pd
import pytest

import rimulabs_trends
from rimulabs_trends import TrendReq, TrendsError

FIXTURES = Path(__file__).parent / "fixtures"


def load(name):
    data = json.loads((FIXTURES / name).read_text(encoding="utf-8"))
    return data["data"] if isinstance(data, dict) else data


class FakeTransport:
    """Records each run input and answers with real Actor output captured on 18 October 2026."""

    def __init__(self, items):
        self.items = items
        self.calls = []

    def __call__(self, run_input):
        self.calls.append(run_input)
        return self.items


@pytest.fixture
def keywords():
    transport = FakeTransport(load("keywords-crm-erp-us.json"))
    client = TrendReq(_transport=transport)
    client.build_payload(["crm", "erp"], timeframe="today 12-m", geo="US")
    return client, transport


def test_interest_over_time_has_pytrends_shape(keywords):
    client, _ = keywords
    df = client.interest_over_time()
    assert list(df.columns) == ["crm", "erp", "isPartial"]
    assert df.index.name == "date"
    assert pd.api.types.is_datetime64_any_dtype(df.index)
    assert df.index.is_monotonic_increasing
    assert df["crm"].dtype.kind == "i" and df["erp"].dtype.kind == "i"
    assert df["isPartial"].dtype == bool
    assert len(df) >= 50  # weekly points for 12 months
    assert df[["crm", "erp"]].to_numpy().max() == 100  # compared on one 0-100 scale


def test_interest_by_region_has_pytrends_shape(keywords):
    client, _ = keywords
    df = client.interest_by_region()
    assert df.index.name == "geoName"
    assert list(df.columns) == ["crm", "erp"]
    assert list(df.index) == sorted(df.index)
    assert "California" in df.index
    coded = client.interest_by_region(inc_geo_code=True)
    assert list(coded.columns) == ["geoCode", "crm", "erp"]
    assert coded.loc["California", "geoCode"] == "US-CA"


def test_city_resolution_is_refused(keywords):
    client, _ = keywords
    with pytest.raises(TrendsError):
        client.interest_by_region(resolution="CITY")


def test_related_queries_has_pytrends_shape(keywords):
    client, _ = keywords
    related = client.related_queries()
    assert set(related) == {"crm", "erp"}
    top = related["crm"]["top"]
    assert list(top.columns) == ["query", "value"]
    assert len(top) > 0
    rising = related["crm"]["rising"]
    assert rising is None or list(rising.columns) == ["query", "value"]


def test_one_run_serves_every_method(keywords):
    client, transport = keywords
    client.interest_over_time()
    client.interest_by_region()
    client.related_queries()
    assert len(transport.calls) == 1


def test_payload_maps_pytrends_arguments(keywords):
    _, transport = keywords
    client = TrendReq(_transport=transport)
    client.build_payload(["crm", "erp"], timeframe="today 12-m", geo="US", gprop="froogle", cat=7)
    client.interest_over_time()
    sent = transport.calls[-1]
    assert sent["searchTerms"] == ["crm", "erp"]
    assert sent["compare"] is True
    assert sent["timeRange"] == "past-12-months"
    assert sent["geo"] == "US"
    assert sent["searchType"] == "shopping"
    assert sent["category"] == 7
    assert set(sent["sections"]) == {"interestOverTime", "interestByRegion", "relatedQueries"}


def test_single_keyword_custom_range_and_default_timeframe():
    transport = FakeTransport([])
    client = TrendReq(_transport=transport)
    client.build_payload(["crm"], timeframe="2026-01-01 2026-06-30")
    client.related_queries()
    assert transport.calls[-1]["compare"] is False
    assert transport.calls[-1]["startDate"] == "2026-01-01"
    assert transport.calls[-1]["endDate"] == "2026-06-30"
    client.build_payload(["crm"])
    client.related_queries()
    assert transport.calls[-1]["timeRange"] == "past-5-years"  # pytrends' default 'today 5-y'


def test_payload_validation():
    client = TrendReq(_transport=FakeTransport([]))
    with pytest.raises(ValueError):
        client.build_payload(["a", "b", "c", "d", "e", "f"])
    with pytest.raises(ValueError):
        client.build_payload(["a"], timeframe="today 2-y")
    with pytest.raises(ValueError):
        client.build_payload(["a"], gprop="maps")
    with pytest.raises(TrendsError):
        TrendReq(_transport=FakeTransport([])).interest_over_time()


def test_missing_token_explains_where_to_get_one(monkeypatch):
    monkeypatch.delenv("APIFY_TOKEN", raising=False)
    with pytest.raises(TrendsError, match="APIFY_TOKEN"):
        TrendReq()


def test_trending_searches():
    transport = FakeTransport(load("trending-gb.json"))
    client = TrendReq(_transport=transport)
    df = client.trending_searches(pn="united_kingdom")
    assert transport.calls[-1] == {"mode": "trendingNow", "trendingGeos": ["GB"], "maxTrendingPerGeo": 20}
    assert list(df.columns) == [0] and len(df) == 5
    realtime = client.realtime_trending_searches(pn="GB", count=5)
    assert list(realtime.columns) == ["title", "approxTraffic", "publishedAt", "news"]
    with pytest.raises(ValueError):
        client.trending_searches(pn="atlantis")


def test_unsupported_methods_say_so(keywords):
    client, _ = keywords
    for call in (client.related_topics, lambda: client.suggestions("x"), client.today_searches,
                 lambda: client.top_charts(2025), client.get_historical_interest):
        with pytest.raises(NotImplementedError):
            call()


def test_http_call(monkeypatch):
    sent = {}

    class Response:
        status_code = 200

        @staticmethod
        def json():
            return load("keywords-crm-erp-us.json")

    def fake_post(url, params=None, json=None, headers=None, timeout=None):
        sent.update(url=url, params=params, json=json, headers=headers, timeout=timeout)
        return Response()

    monkeypatch.setattr(rimulabs_trends.requests, "post", fake_post)
    client = TrendReq(apify_token="test-token")
    client.build_payload(["crm", "erp"], timeframe="today 12-m", geo="US")
    assert len(client.interest_over_time()) > 0
    assert sent["url"] == "https://api.apify.com/v2/acts/rimulabs~google-trends-insights/run-sync-get-dataset-items"
    assert sent["headers"]["Authorization"] == "Bearer test-token"
    assert sent["params"] == {"timeout": 300}
    assert sent["json"]["searchTerms"] == ["crm", "erp"]


def test_http_errors_are_readable(monkeypatch):
    class Response:
        status_code = 401
        text = "unauthorized"

        @staticmethod
        def json():
            return {"error": {"message": "User was not found or authentication token is not valid"}}

    monkeypatch.setattr(rimulabs_trends.requests, "post", lambda *a, **k: Response())
    client = TrendReq(apify_token="bad")
    client.build_payload(["crm"])
    with pytest.raises(TrendsError, match="401.*token is not valid"):
        client.interest_over_time()
