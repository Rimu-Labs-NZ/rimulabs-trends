"""A drop-in replacement for pytrends' ``TrendReq``, backed by a maintained Google Trends API.

pytrends is archived and often fails with HTTP 429. This class keeps the same calls and returns
the same pandas shapes, but each request runs the hosted Google Trends Scraper on Apify
(https://apify.com/rimulabs/google-trends-insights), which paces and retries requests for you.

    from rimulabs_trends import TrendReq   # was: from pytrends.request import TrendReq

    pytrends = TrendReq()                  # reads APIFY_TOKEN from the environment
    pytrends.build_payload(["crm", "erp"], timeframe="today 12-m", geo="US")
    df = pytrends.interest_over_time()

Not affiliated with pytrends or Google.
"""

import os
import re

import pandas as pd
import requests

__version__ = "0.1.0"
__all__ = ["TrendReq", "TrendsError", "__version__"]

ACTOR = "rimulabs~google-trends-insights"
API = "https://api.apify.com/v2"

TIMEFRAMES = {
    "now 1-H": "past-hour",
    "now 4-H": "past-4-hours",
    "now 1-d": "past-day",
    "now 7-d": "past-7-days",
    "today 1-m": "past-30-days",
    "today 3-m": "past-90-days",
    "today 12-m": "past-12-months",
    "today 5-y": "past-5-years",
    "all": "since-2004",
}
GPROPS = {"": "web", "images": "images", "news": "news", "froogle": "shopping", "youtube": "youtube"}
# pytrends' trending_searches() country names, mapped to the ISO codes the API uses.
COUNTRIES = {
    "argentina": "AR", "australia": "AU", "austria": "AT", "belgium": "BE", "brazil": "BR",
    "canada": "CA", "chile": "CL", "colombia": "CO", "czech_republic": "CZ", "denmark": "DK",
    "egypt": "EG", "finland": "FI", "france": "FR", "germany": "DE", "greece": "GR",
    "hong_kong": "HK", "hungary": "HU", "india": "IN", "indonesia": "ID", "ireland": "IE",
    "israel": "IL", "italy": "IT", "japan": "JP", "kenya": "KE", "malaysia": "MY", "mexico": "MX",
    "netherlands": "NL", "new_zealand": "NZ", "nigeria": "NG", "norway": "NO", "philippines": "PH",
    "poland": "PL", "portugal": "PT", "romania": "RO", "saudi_arabia": "SA", "singapore": "SG",
    "south_africa": "ZA", "south_korea": "KR", "spain": "ES", "sweden": "SE", "switzerland": "CH",
    "taiwan": "TW", "thailand": "TH", "turkey": "TR", "ukraine": "UA", "united_kingdom": "GB",
    "united_states": "US", "vietnam": "VN",
}
_CUSTOM_RANGE = re.compile(r"^(\d{4}-\d{2}-\d{2}) (\d{4}-\d{2}-\d{2})$")


class TrendsError(Exception):
    """Raised when the Google Trends API run fails or returns no data."""


class TrendReq:
    """pytrends-compatible client. Get a token at https://console.apify.com/settings/integrations.

    pytrends' connection arguments (hl, tz, timeout, proxies, retries, backoff_factor,
    requests_args) are accepted so existing code keeps working, but pacing, retries and proxies
    are handled by the API.
    """

    def __init__(self, hl="en-US", tz=360, geo="", timeout=(2, 5), proxies="", retries=0,
                 backoff_factor=0, requests_args=None, apify_token=None, run_timeout=300,
                 _transport=None):
        self.hl = hl
        self.tz = tz
        self.geo = geo
        self.token = apify_token or os.environ.get("APIFY_TOKEN")
        self.run_timeout = run_timeout
        self._transport = _transport
        if not self.token and not _transport:
            raise TrendsError(
                "Set the APIFY_TOKEN environment variable or pass apify_token=. "
                "Get a token at https://console.apify.com/settings/integrations."
            )
        self.kw_list = []
        self._input = None
        self._items = None

    # ---- payload -------------------------------------------------------------------------------

    def build_payload(self, kw_list, cat=0, timeframe="today 5-y", geo="", gprop=""):
        """Same arguments as pytrends. Up to 5 keywords are compared on one 0-100 scale."""
        if isinstance(kw_list, str):
            kw_list = [kw_list]
        kw_list = [str(k) for k in kw_list]
        if not 1 <= len(kw_list) <= 5:
            raise ValueError("kw_list must contain 1 to 5 keywords, as in pytrends.")
        if gprop not in GPROPS:
            raise ValueError(f"gprop must be one of {sorted(GPROPS)}.")
        run_input = {
            "mode": "keywords",
            "searchTerms": kw_list,
            "compare": len(kw_list) > 1,
            "geo": geo or self.geo or "",
            "searchType": GPROPS[gprop],
            "category": int(cat or 0),
            "sections": ["interestOverTime", "interestByRegion", "relatedQueries"],
        }
        run_input.update(_time_range(timeframe))
        self.kw_list = kw_list
        self._input = run_input
        self._items = None

    # ---- keyword data --------------------------------------------------------------------------

    def interest_over_time(self):
        """DataFrame indexed by date, one column per keyword plus ``isPartial``."""
        items = self._keyword_items()
        frames = {}
        partial = {}
        for item in items:
            for point in item.get("interestOverTime") or []:
                frames.setdefault(point["date"], {})[item["keyword"]] = point.get("value")
                partial[point["date"]] = bool(point.get("isPartial"))
        if not frames:
            return pd.DataFrame()
        df = pd.DataFrame.from_dict(frames, orient="index")
        df = df.reindex(columns=self.kw_list)
        df.index = pd.to_datetime(df.index)
        df.index.name = "date"
        df = df.sort_index().fillna(0).astype(int)
        df["isPartial"] = [partial[d.strftime("%Y-%m-%d")] for d in df.index]
        return df

    def interest_by_region(self, resolution="COUNTRY", inc_low_vol=False, inc_geo_code=False):
        """DataFrame indexed by ``geoName``, one column per keyword (plus ``geoCode`` if asked).

        Worldwide payloads return countries and country payloads return their regions, as with
        pytrends' defaults. City and metro (DMA) resolution aren't available.
        """
        if str(resolution).upper() in ("CITY", "DMA"):
            raise TrendsError("City and DMA resolution aren't available; use COUNTRY or REGION.")
        rows = {}
        codes = {}
        for item in self._keyword_items():
            for region in item.get("interestByRegion") or []:
                rows.setdefault(region["geoName"], {})[item["keyword"]] = region.get("value")
                codes[region["geoName"]] = region.get("geoCode")
        if not rows:
            return pd.DataFrame()
        df = pd.DataFrame.from_dict(rows, orient="index").reindex(columns=self.kw_list)
        df = df.fillna(0).astype(int).sort_index()
        df.index.name = "geoName"
        if not inc_low_vol:
            df = df[(df[self.kw_list] > 0).any(axis=1)]
        if inc_geo_code:
            df.insert(0, "geoCode", [codes[name] for name in df.index])
        return df

    def related_queries(self):
        """``{keyword: {"top": DataFrame(query, value), "rising": DataFrame(query, value)}}``."""
        result = {}
        for item in self._keyword_items():
            related = item.get("relatedQueries") or {}
            result[item["keyword"]] = {
                kind: (pd.DataFrame([{"query": q["query"], "value": q["value"]} for q in related.get(kind) or []],
                                    columns=["query", "value"]) if related.get(kind) else None)
                for kind in ("top", "rising")
            }
        for keyword in self.kw_list:
            result.setdefault(keyword, {"top": None, "rising": None})
        return result

    # ---- trending ------------------------------------------------------------------------------

    def trending_searches(self, pn="united_states"):
        """Today's trending searches for a country: a one-column DataFrame, as in pytrends."""
        items = self._trending(pn, 20)
        return pd.DataFrame([item["title"] for item in items])

    def realtime_trending_searches(self, pn="US", cat="all", count=300):
        """Trending searches with ``title``, ``approxTraffic``, ``publishedAt`` and ``news``."""
        items = self._trending(pn, min(int(count), 300))
        return pd.DataFrame(items, columns=["title", "approxTraffic", "publishedAt", "news"])

    # ---- not available -------------------------------------------------------------------------

    def related_topics(self):
        raise NotImplementedError("related_topics() isn't available; Google returns no topic data for most queries.")

    def suggestions(self, keyword):
        raise NotImplementedError("suggestions() isn't available.")

    def today_searches(self, pn="US"):
        raise NotImplementedError("today_searches() isn't available; use trending_searches().")

    def top_charts(self, date, hl="en-US", tz=300, geo="GLOBAL"):
        raise NotImplementedError("top_charts() isn't available.")

    def get_historical_interest(self, *args, **kwargs):
        raise NotImplementedError("get_historical_interest() isn't available; use a custom range: "
                                  "timeframe='2026-01-01 2026-06-30'.")

    # ---- internals -----------------------------------------------------------------------------

    def _keyword_items(self):
        if self._input is None:
            raise TrendsError("Call build_payload() first.")
        if self._items is None:
            # One run fetches every section, so calling several methods costs one keyword report each.
            self._items = self._run(self._input)
        return self._items

    def _trending(self, pn, limit):
        code = COUNTRIES.get(str(pn).lower(), str(pn).upper())
        if not re.fullmatch(r"[A-Z]{2}", code):
            raise ValueError(f"Unknown country {pn!r}: use a pytrends name such as 'united_states' or a code such as 'US'.")
        return self._run({"mode": "trendingNow", "trendingGeos": [code], "maxTrendingPerGeo": limit})

    def _run(self, run_input):
        if self._transport:
            return self._transport(run_input)
        response = requests.post(
            f"{API}/acts/{ACTOR}/run-sync-get-dataset-items",
            params={"timeout": self.run_timeout},
            json=run_input,
            headers={"Authorization": f"Bearer {self.token}", "User-Agent": f"rimulabs-trends/{__version__}"},
            timeout=self.run_timeout + 30,
        )
        if response.status_code >= 400:
            try:
                message = response.json().get("error", {}).get("message")
            except ValueError:
                message = None
            raise TrendsError(f"Google Trends API returned HTTP {response.status_code}: {message or response.text[:200]}")
        items = response.json()
        if not isinstance(items, list):
            raise TrendsError("Unexpected response from the Google Trends API.")
        return items


def _time_range(timeframe):
    timeframe = (timeframe or "today 5-y").strip()
    if timeframe in TIMEFRAMES:
        return {"timeRange": TIMEFRAMES[timeframe]}
    custom = _CUSTOM_RANGE.match(timeframe)
    if custom:
        return {"startDate": custom.group(1), "endDate": custom.group(2)}
    raise ValueError(f"Unsupported timeframe {timeframe!r}. Use one of {sorted(TIMEFRAMES)} "
                     "or a custom range like '2026-01-01 2026-06-30'.")
