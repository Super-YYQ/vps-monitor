"""Offline tests for the aggregator-site (mirror) fallback."""

import json

import pytest

from app.detectors import Result, inspect_monitor
from app.fetcher import Page
from app.mirror import mirror_result
from app.models import Monitor


@pytest.fixture
def whmcs_config():
    return Monitor(
        provider="DMIT",
        name="LAX.Pro.WEE",
        url="https://www.dmit.io/cart.php?a=add&pid=183",
        adapter="whmcs",
        expected_text="LAX.Pro.WEE",
        out_of_stock=["Out of Stock", "缺货"],
        in_stock=["In Stock", "有货"],
    )


@pytest.fixture
def sources(tmp_path, monkeypatch):
    data = {
        "sources": [
            {"id": "t1", "name": "测试聚合站", "url": "https://mirror.example/list"},
            {"id": "t2", "name": "备用聚合站", "url": "https://alt.example/list"},
        ]
    }
    path = tmp_path / "mirror_sources.json"
    path.write_text(json.dumps(data), encoding="utf-8")
    monkeypatch.setattr("app.mirror.SOURCES_FILE", path)
    return data["sources"]


def challenge_page(url):
    return Page(
        403,
        '<title>Just a moment...</title><form id="challenge-form"></form>',
        url,
        26,
        challenge=True,
    )


def test_mirror_reports_in_stock_near_product(whmcs_config, sources):
    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><h1>今日补货</h1><p>DMIT LAX.Pro.WEE 补货啦，In Stock</p>"
                "<p>" + "分隔填充 " * 60 + "</p>"
                "<p>其他商家缺货记录</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    result = mirror_result(whmcs_config, fetch, sources)
    assert result.status == "in_stock" and "聚合站" in result.reason


def test_mirror_out_of_stock_marker_wins(whmcs_config, sources):
    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><p>DMIT LAX.Pro.WEE 已售罄 Out of Stock</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    result = mirror_result(whmcs_config, fetch, sources)
    assert result.status == "out_of_stock"


def test_mirror_ignores_stock_marker_far_from_product(whmcs_config, sources):
    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><p>DMIT LAX.Pro.WEE</p>"
                "<p>" + "填充内容 " * 80 + "</p>"
                "<p>某个别的产品 In Stock 有货</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    result = mirror_result(whmcs_config, fetch, sources)
    # No marker inside the product window: unknown, not a false positive.
    assert result is None or result.status == "unknown"


def test_mirror_skips_source_without_provider(whmcs_config, sources):
    # First source lacks the provider; the poller should skip it and, once the
    # second source's interval elapses, rotate to it and find the product.
    from app.mirror import MirrorPoller

    now = [0.0]

    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(200, "<html><body><p>别的商家 Other LAX.Pro.WEE</p></body></html>", url, 30)
        return Page(200, "<html><body><p>DMIT LAX.Pro.WEE 有货</p></body></html>", url, 31)

    poller = MirrorPoller(sources=sources, clock=lambda: now[0])
    first = mirror_result(whmcs_config, fetch, poller=poller)
    assert first is None  # first source has no provider match

    # Advance past the second source's staggered interval.
    now[0] += 601
    second = mirror_result(whmcs_config, fetch, poller=poller)
    assert second is not None and second.status == "in_stock" and "备用聚合站" in second.reason


def test_mirror_returns_none_when_all_sources_unreachable(whmcs_config, sources):
    def fetch(url):
        return challenge_page(url)

    assert mirror_result(whmcs_config, fetch, sources) is None


def test_inspect_monitor_uses_mirror_on_cloudflare_block(whmcs_config, sources):
    calls = []

    def fetch(url):
        calls.append(url)
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><p>DMIT LAX.Pro.WEE In Stock 有货</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    result = inspect_monitor(whmcs_config, fetch, mirror=lambda c, f: mirror_result(c, f, sources))
    assert result.status == "in_stock" and "聚合站" in result.reason
    assert any(url.startswith("https://mirror.example") for url in calls)


def test_inspect_monitor_without_mirror_keeps_error(whmcs_config):
    def fetch(url):
        return challenge_page(url)

    result = inspect_monitor(whmcs_config, fetch, mirror=None)
    assert result.status == "error" and "Cloudflare" in result.reason


def test_inspect_monitor_mirror_not_consulted_for_html_adapter(whmcs_config, sources):
    html_config = whmcs_config.model_copy(
        update={"adapter": "html", "selector": "div.product", "url": "https://www.dmit.io/wee"}
    )

    def fetch(url):
        return challenge_page(url)

    seen = []

    def mirror(config, f):
        seen.append("called")
        return Result("in_stock", "should not happen")

    result = inspect_monitor(html_config, fetch, mirror=mirror)
    assert seen == [] and result.status == "error"


def test_mirror_setting_toggle(authenticated):
    default = authenticated.get("/api/settings/mirror").json()
    assert default == {"enabled": True}
    assert authenticated.put("/api/settings/mirror", json={"enabled": False}).json()["enabled"] is False
    assert authenticated.get("/api/settings/mirror").json() == {"enabled": False}
    assert authenticated.put("/api/settings/mirror", json={"enabled": True}).json()["enabled"] is True


def test_poller_rate_limits_each_source(whmcs_config, sources):
    from app.mirror import MirrorPoller

    calls = []
    now = [0.0]

    def fetch(url):
        calls.append(url)
        return Page(200, "<html><body><p>DMIT LAX.Pro.WEE 有货</p></body></html>", url, 30)

    poller = MirrorPoller(sources=sources, clock=lambda: now[0])
    # First call refreshes source t1 and caches it.
    assert mirror_result(whmcs_config, fetch, poller=poller).status == "in_stock"
    assert len(calls) == 1

    # Immediately after, no source is due: no new network call, cached page reused.
    assert mirror_result(whmcs_config, fetch, poller=poller).status == "in_stock"
    assert len(calls) == 1

    # Advance past t1's interval: next call rotates to t2 (round-robin).
    now[0] += 601
    assert mirror_result(whmcs_config, fetch, poller=poller).status == "in_stock"
    assert len(calls) == 2
    assert calls[1].startswith("https://alt.example")


def test_poller_shares_cache_across_monitors(whmcs_config, sources):
    from app.mirror import MirrorPoller

    calls = []

    def fetch(url):
        calls.append(url)
        return Page(200, "<html><body><p>DMIT LAX.Pro.WEE 有货</p></body></html>", url, 30)

    poller = MirrorPoller(sources=sources)
    # Two different monitor configs share the same poller: only one fetch total.
    other = whmcs_config.model_copy(update={"name": "LAX.Pro.WEE"})
    assert mirror_result(whmcs_config, fetch, poller=poller).status == "in_stock"
    assert mirror_result(other, fetch, poller=poller).status == "in_stock"
    assert len(calls) == 1


def test_mirror_matches_via_alias(whmcs_config, sources):
    # Aggregator sites use their own naming (e.g. "tri basic" instead of
    # "US.LA.TRI.Basic"); configured aliases let the mirror still match.
    config = whmcs_config.model_copy(
        update={"provider": "VMISS", "name": "US.LA.TRI.Basic", "mirror_aliases": ["tri basic"]}
    )

    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><p>vmiss 洛杉矶 tri basic ❌ 无货 Out of Stock</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    result = mirror_result(config, fetch, sources)
    assert result is not None and result.status == "out_of_stock" and "聚合站" in result.reason


def test_mirror_alias_context_window_centers_on_alias(whmcs_config, sources):
    # The stock marker check uses the text window around the alias, so a
    # marker next to the alias is found even when the official name is absent.
    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><p>dmit 洛杉矶pro-lax.an5.pro.tiny $14.9/月 缺货 Sold Out</p>"
                "<p>" + "填充 " * 100 + "</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    result = mirror_result(whmcs_config.model_copy(update={"mirror_aliases": ["an5.pro.tiny"]}), fetch, sources)
    assert result is not None and result.status == "out_of_stock"


def test_mirror_without_alias_still_no_match(whmcs_config, sources):
    # Without a configured alias, the aggregator's own naming must NOT match.
    def fetch(url):
        if url.startswith("https://mirror.example"):
            return Page(
                200,
                "<html><body><p>vmiss 洛杉矶 tri basic ❌ 无货 Out of Stock</p></body></html>",
                url,
                30,
            )
        return challenge_page(url)

    assert mirror_result(whmcs_config, fetch, sources) is None
