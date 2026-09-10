"""Rate-limited, round-robin fallback for public aggregator sites."""

import json
import threading
import time
from dataclasses import dataclass
from pathlib import Path

from app.fetcher import Page, fetch_page
from app.models import public_url

SOURCES_FILE = Path(__file__).parent / "mirror_sources.json"
DEFAULT_INTERVAL = 600


@dataclass
class SourceState:
    page: Page | None = None
    next_fetch: float = 0.0


class MirrorPoller:
    """Share cached pages and rate limits across all monitor checks."""

    def __init__(self, sources=None, clock=None):
        self.sources = list(load_sources() if sources is None else sources)
        self.clock = clock or time.monotonic
        self.states = {
            source.get("id", source.get("url", "")): SourceState() for source in self.sources
        }
        self.cursor = 0
        self.lock = threading.Lock()
        # Stagger initial fetch times so the first call refreshes only the first
        # source; the rest become due only after their own interval elapses.
        for i, source in enumerate(self.sources):
            ident = source.get("id", source.get("url", ""))
            interval = max(60, int(source.get("interval", DEFAULT_INTERVAL)))
            self.states[ident].next_fetch = self.clock() + i * interval

    def _due_source(self, now):
        if not self.sources:
            return None
        for offset in range(len(self.sources)):
            index = (self.cursor + offset) % len(self.sources)
            source = self.sources[index]
            ident = source.get("id", source.get("url", ""))
            if now >= self.states[ident].next_fetch:
                self.cursor = (index + 1) % len(self.sources)
                return source
        return None

    def refresh_one(self, fetch, now=None):
        """Refresh at most one due source, returning its current page if any.

        A source is refreshed only when it is genuinely due (its interval has
        elapsed). The very first call refreshes the first source; subsequent
        calls reuse cached pages until a source's interval elapses, so many
        monitors sharing this poller do not hammer the aggregator sites.
        """
        now = self.clock() if now is None else now
        with self.lock:
            source = self._due_source(now)
            if source is None:
                return
            ident = source.get("id", source.get("url", ""))
            state = self.states[ident]
            interval = max(60, int(source.get("interval", DEFAULT_INTERVAL)))
            # Reserve the slot before network I/O, so concurrent checks cannot duplicate it.
            state.next_fetch = now + interval
        try:
            url = source["url"]
            public_url(url)
            page = fetch(url)
        except Exception:
            return
        if page.status == 200 and not page.challenge:
            with self.lock:
                state.page = page

    def pages(self):
        with self.lock:
            return [state.page for state in self.states.values() if state.page is not None]


def load_sources():
    """Return the bundled mirror source list, or [] when unavailable."""
    try:
        return json.loads(SOURCES_FILE.read_text(encoding="utf-8")).get("sources", [])
    except Exception:
        return []


def _result_from_page(config, page, label):
    from bs4 import BeautifulSoup

    from app.detectors import Result, normalize

    provider = normalize(config.provider)
    product = normalize(config.name)
    soup = BeautifulSoup(page.text, "html.parser")
    for element in soup.select("script, style, template, noscript, [hidden], [aria-hidden='true']"):
        element.decompose()
    text = normalize(soup.get_text(" ", strip=True))
    if provider not in text:
        return None
    index = text.find(product)
    if index < 0:
        return None
    context = text[max(0, index - 80) : index + len(product) + 160]
    if any(normalize(term) in context for term in config.out_of_stock):
        return Result("out_of_stock", f"聚合站 {label} 显示缺货（官网 Cloudflare 拦截备用源）", page.latency)
    if any(normalize(term) in context for term in config.in_stock):
        return Result("in_stock", f"聚合站 {label} 显示有货（官网 Cloudflare 拦截备用源）", page.latency)
    return Result("unknown", f"聚合站 {label} 找到产品但无明确库存标记", page.latency)


_DEFAULT_POLLER = MirrorPoller()


def mirror_result(config, fetch=fetch_page, sources=None, poller=None):
    """Refresh one due source, then inspect all cached source pages.

    Passing ``sources`` creates an isolated poller, which keeps tests and
    callers with custom source lists independent. Production callers share the
    process-wide poller and therefore share the rate limit.
    """
    if sources is not None:
        poller = MirrorPoller(sources=sources)
    poller = poller or _DEFAULT_POLLER
    poller.refresh_one(fetch)
    for source, page in zip(poller.sources, poller.pages()):
        result = _result_from_page(config, page, source.get("name", source.get("url", "")))
        if result is not None:
            return result
    return None
