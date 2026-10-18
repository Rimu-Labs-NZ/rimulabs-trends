# rimulabs-trends: a drop-in replacement for pytrends

`pytrends` is archived and its last release was in April 2023. Google rate-limits Trends heavily, so pytrends scripts now stop with HTTP 429 "Too Many Requests" errors.

`rimulabs-trends` keeps pytrends' `TrendReq` interface and returns the same pandas DataFrames, but each request runs a maintained, hosted [Google Trends API](https://apify.com/rimulabs/google-trends-insights) on Apify. That service paces and retries requests for you. Change one import and keep your analysis code.

```bash
pip install rimulabs-trends
```

```python
# from pytrends.request import TrendReq
from rimulabs_trends import TrendReq

pytrends = TrendReq()  # reads the APIFY_TOKEN environment variable
pytrends.build_payload(["crm", "erp"], timeframe="today 12-m", geo="US")

df = pytrends.interest_over_time()        # date index, one column per keyword, plus isPartial
regions = pytrends.interest_by_region()   # geoName index, one column per keyword
related = pytrends.related_queries()      # {"crm": {"top": DataFrame, "rising": DataFrame}, ...}
today = pytrends.trending_searches(pn="united_states")
```

## Setup

1. Create an Apify account and copy your API token from [Settings → Integrations](https://console.apify.com/settings/integrations).
2. Set it as `APIFY_TOKEN`, or pass `TrendReq(apify_token="...")`.

## What's supported

| pytrends | Status |
|---|---|
| `build_payload(kw_list, cat, timeframe, geo, gprop)` | Supported. Up to 5 keywords, compared on one 0-100 scale. |
| `interest_over_time()` | Supported, same shape, including `isPartial`. |
| `interest_by_region(resolution, inc_low_vol, inc_geo_code)` | Countries (worldwide) or regions (inside a country). City and DMA resolution aren't available. |
| `related_queries()` | Supported: `top` and `rising` DataFrames with `query` and `value`. |
| `trending_searches(pn)` | Supported. Accepts pytrends country names (`"united_kingdom"`) or ISO codes (`"GB"`). |
| `realtime_trending_searches(pn, count)` | Returns `title`, `approxTraffic`, `publishedAt` and `news`. |
| `related_topics()`, `suggestions()`, `today_searches()`, `top_charts()`, `get_historical_interest()` | Not available. They raise `NotImplementedError` with a pointer to the alternative. |

**Timeframes:** `now 1-H`, `now 4-H`, `now 1-d`, `now 7-d`, `today 1-m`, `today 3-m`, `today 12-m`, `today 5-y` (the default), `all`, or a custom `"2026-01-01 2026-06-30"`. **gprop:** `""`, `images`, `news`, `froogle` (Google Shopping) and `youtube`.

pytrends' connection arguments (`hl`, `tz`, `timeout`, `proxies`, `retries`, `backoff_factor`, `requests_args`) are accepted so existing code runs unchanged, but the hosted API handles pacing, retries and proxies itself.

## Cost

The hosted API is pay-per-use on Apify: **$3.00 per 1,000 keyword reports** and **$0.50 per 1,000 trending searches**, plus Apify's small per-run fee. One `build_payload()` fetches every section in a single run, so calling `interest_over_time()`, `interest_by_region()` and `related_queries()` on the same payload is charged once per keyword. Apify's free plan includes monthly credit that covers casual use.

## Good to know

- Values are relative interest from 0 to 100, not search volumes, exactly as on the Google Trends website.
- Each call runs synchronously and usually returns in 10-60 seconds. Raise `run_timeout` (in seconds, default 300) for slow payloads.
- Errors from the API raise `rimulabs_trends.TrendsError` with Apify's message, for example an invalid token or insufficient credit.

More detail and a method-by-method migration guide: [rimulabs.com/guides/pytrends-alternative](https://rimulabs.com/guides/pytrends-alternative).

---

MIT licensed. Made by [Rimu Labs](https://rimulabs.com), which also maintains the hosted API. Not affiliated with pytrends or Google. Issues and questions: [GitHub issues](https://github.com/Rimu-Labs-NZ/rimulabs-trends/issues) or support@rimulabs.com. This package and its documentation were written by the AI assistant for Rimu Labs.
