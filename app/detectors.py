import json
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, urlsplit

from bs4 import BeautifulSoup

from app.fetcher import Page
from app.models import Monitor


@dataclass
class Result:
    status: str
    reason: str
    latency: int = 0


def normalize(text):
    return " ".join(text.split()).casefold()


def detect(config: Monitor, page: Page) -> Result:
    def result(status, reason):
        return Result(status, reason, page.latency)

    if page.status != 200:
        return result("error", f"HTTP {page.status}；未作为库存变化")
    soup = BeautifulSoup(page.text, "html.parser")
    title = normalize(soup.title.get_text() if soup.title else "")
    if any(x in title for x in ["just a moment", "attention required", "access denied", "sign in", "login"]):
        return result("unknown", "收到验证或登录页面")
    if soup.select_one("#challenge-form, #cf-challenge-running, .g-recaptcha, .h-captcha"):
        return result("unknown", "页面包含人机验证，需人工核实")
    if config.adapter == "json":
        try:
            value = json.loads(page.text)
            for key in config.json_path.split("."):
                value = value[int(key)] if isinstance(value, list) else value[key]
            if isinstance(value, (dict, list)):
                raise ValueError("字段不是标量")
            text = json.dumps(value, ensure_ascii=False) if not isinstance(value, str) else value
        except (ValueError, TypeError, KeyError, IndexError):
            return result("unknown", "JSON 库存字段不存在或结构已变化")
        stock = normalize(text) in [normalize(x) for x in config.in_stock]
        empty = normalize(text) in [normalize(x) for x in config.out_of_stock]
    else:
        for element in soup.select("script, style, template, noscript, [hidden], [aria-hidden='true']"):
            element.decompose()
        if config.adapter == "whmcs":
            old = parse_qs(urlsplit(config.url).query).get("pid")
            new = parse_qs(urlsplit(page.url).query).get("pid")
            if not old or old != new:
                return result("unknown", "WHMCS 产品编号丢失或已跳转，请更新检测地址")
            text = normalize(soup.get_text(" ", strip=True))
            if any(normalize(term) in text for term in config.out_of_stock):
                return result("out_of_stock", "产品订购页面明确显示缺货")
            if not config.expected_text or normalize(config.expected_text) not in text:
                return result("unknown", "页面未出现预期产品名称")
            if soup.select_one("input[type=password]") and "configure" not in text:
                return result("unknown", "需要登录，无法确认库存")
            stock = bool(soup.select_one("#frmConfigureProduct, form#frmConfigureProduct"))
            empty = False
        else:
            if not config.selector:
                return result("unknown", "尚未配置产品区域选择器")
            try:
                nodes = soup.select(config.selector)
            except Exception:
                return result("unknown", "产品区域选择器无效")
            if config.product_match:
                nodes = [
                    node for node in nodes if normalize(config.product_match) in normalize(node.get_text(" "))
                ]
            if len(nodes) != 1:
                return result("unknown", f"产品区域匹配到 {len(nodes)} 项，必须精确匹配一项")
            text = normalize(nodes[0].get_text(" ", strip=True))
            if config.expected_text and normalize(config.expected_text) not in text:
                return result("unknown", "产品区域未出现预期文字")
            empty = any(normalize(term) in text for term in config.out_of_stock)
            # Negative markers have priority: 'not available' contains 'available'.
            stock = any(normalize(term) in text for term in config.in_stock)
        if empty:
            return result("out_of_stock", "产品区域匹配缺货标记")
    if empty:
        return result("out_of_stock", "库存字段匹配缺货值")
    if stock:
        return result("in_stock", "产品区域匹配有货标记")
    return result("unknown", "未找到明确库存证据；页面可能变更或由 JavaScript 加载")


def discover_whmcs(page: Page):
    """Read product cards only, never submit forms or create orders."""
    if page.status != 200:
        raise ValueError(f"无法读取产品目录：HTTP {page.status}")
    soup = BeautifulSoup(page.text, "html.parser")
    items = []
    seen = set()
    for anchor in soup.select("a[href]"):
        from urllib.parse import urljoin

        url = urljoin(page.url, anchor["href"])
        query = parse_qs(urlsplit(url).query)
        pid = query.get("pid", [""])[0]
        if not re.fullmatch(r"\d+", pid) or pid in seen:
            continue
        if urlsplit(url).hostname != urlsplit(page.url).hostname:
            continue
        card = anchor.find_parent(class_="product") or anchor.find_parent("tr")
        if card is None:
            continue
        heading = card.select_one("h3, h4, .product-name")
        if heading is None:
            continue
        name = heading.get_text(" ", strip=True)
        seen.add(pid)
        items.append({"name": name, "url": url, "pid": pid, "specs": card.get_text(" ", strip=True)[:600]})
    return items[:100]


def inspect_monitor(config: Monitor, fetch):
    """Follow an exact WHMCS product card; never claim stock based on Order Now alone."""
    page = fetch(config.url)
    if config.adapter != "whmcs" or parse_qs(urlsplit(config.url).query).get("pid"):
        return detect(config, page)
    initial = detect(config, page)
    if initial.status == "error" or "验证" in initial.reason or "登录" in initial.reason:
        return initial
    soup = BeautifulSoup(page.text, "html.parser")
    matching = []
    for card in soup.select(".product"):
        heading = card.select_one("h3, h4, .product-name")
        if heading and normalize(heading.get_text(" ", strip=True)) == normalize(config.expected_text):
            matching.append(card)
    if len(matching) != 1:
        return Result("unknown", "目录中未找到唯一目标机型；请核对名称或填写商品 PID", page.latency)
    card_text = normalize(matching[0].get_text(" ", strip=True))
    if any(normalize(term) in card_text for term in config.out_of_stock):
        return Result("out_of_stock", "目标商品卡片明确标注缺货", page.latency)
    items = [
        item for item in discover_whmcs(page) if normalize(item["name"]) == normalize(config.expected_text)
    ]
    if len(items) != 1:
        return Result("unknown", "没有唯一商品 PID，不能仅凭 Order Now 判为有货", page.latency)
    import time

    time.sleep(5)
    direct = config.model_copy(update={"url": items[0]["url"]})
    return detect(direct, fetch(direct.url))
