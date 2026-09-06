import pytest

from app.detectors import detect, discover_whmcs
from app.fetcher import Page
from app.models import Monitor


@pytest.mark.parametrize(
    "body,status",
    [
        (
            "<table><tr><td>LAX Basic Sold Out</td><td>Buy now</td></tr><tr><td>LAX Plus Buy now</td></tr></table>",
            "out_of_stock",
        ),
        ("<table><tr><td>LAX Basic Buy now</td></tr></table>", "in_stock"),
        ("<table><tr><td>LAX Plus Buy now</td></tr></table>", "unknown"),
        ("<table><tr><td>LAX Basic Buy now</td></tr><tr><td>LAX Basic Sold Out</td></tr></table>", "unknown"),
        ("<title>Just a moment...</title><table><tr><td>LAX Basic Buy now</td></tr></table>", "unknown"),
        ('<form id="challenge-form"></form><tr><td>LAX Basic Buy now</td></tr>', "unknown"),
        ("<tr><td>LAX Basic <script>Buy now</script></td></tr>", "unknown"),
        ("<tr hidden><td>LAX Basic Buy now</td></tr>", "unknown"),
        ('<div id="app"></div><script>stock=true</script>', "unknown"),
    ],
)
def test_product_scope_and_fail_closed(config, body, status):
    assert detect(Monitor(**config), Page(200, body, config["url"])).status == status


@pytest.mark.parametrize("code", [302, 403, 404, 429, 500, 503])
def test_http_errors_never_report_stock(config, code):
    assert (
        detect(Monitor(**config), Page(code, "<tr>LAX Basic Buy now</tr>", config["url"])).status == "error"
    )


@pytest.mark.parametrize(
    "body,expected",
    [
        ('{"data":{"stock":true}}', "in_stock"),
        ('{"data":{"stock":false}}', "out_of_stock"),
        ('{"data":{"stock":"not true"}}', "unknown"),
        ('{"data":{"stock":null}}', "unknown"),
        ('{"data":{}}', "unknown"),
        ('{"data":{"stock":{}}}', "unknown"),
        ("<html>Captcha</html>", "unknown"),
    ],
)
def test_json_requires_exact_scalar(body, expected):
    c = Monitor(
        provider="Test",
        name="SKU",
        adapter="json",
        json_path="data.stock",
        in_stock=["true"],
        out_of_stock=["false"],
    )
    assert detect(c, Page(200, body, "https://example.com")).status == expected


def test_json_array_path():
    c = Monitor(
        provider="Test",
        name="SKU",
        adapter="json",
        json_path="data.0.stock",
        in_stock=["5"],
        out_of_stock=["0"],
    )
    assert detect(c, Page(200, '{"data":[{"stock":5}]}', "https://example.com")).status == "in_stock"


@pytest.mark.parametrize(
    "body,expected",
    [
        ('<h1>LAX.Basic</h1><form id="frmConfigureProduct"></form>', "in_stock"),
        ("<h1>Out of Stock</h1>", "out_of_stock"),
        ("<h1>LAX.Basic</h1><a>Order Now</a>", "unknown"),
        ('<h1>OTHER.SKU</h1><form id="frmConfigureProduct"></form>', "unknown"),
        ('<title>Login</title><h1>LAX.Basic</h1><form id="frmConfigureProduct"></form>', "unknown"),
    ],
)
def test_whmcs_requires_product_identity_and_config_form(body, expected):
    url = "https://example.com/cart.php?a=add&pid=123"
    c = Monitor(provider="Test", name="LAX.Basic", adapter="whmcs", url=url, expected_text="LAX.Basic")
    assert detect(c, Page(200, body, url)).status == expected


def test_whmcs_redirect_to_different_pid():
    c = Monitor(
        provider="Test",
        name="SKU",
        adapter="whmcs",
        url="https://example.com/cart.php?pid=123",
        expected_text="SKU",
    )
    assert (
        detect(
            c, Page(200, 'SKU <form id="frmConfigureProduct"></form>', "https://example.com/cart.php?pid=456")
        ).status
        == "unknown"
    )


def test_whmcs_catalog_discovery_excludes_other_hosts():
    page = Page(
        200,
        """<div class="product"><h3>LAX Basic</h3><a href="cart.php?a=add&pid=21">Order</a></div>
        <div class="product"><h3>Bad</h3><a href="https://other.example/cart.php?pid=2">Order</a></div>""",
        "https://example.com/index.php",
    )
    items = discover_whmcs(page)
    assert len(items) == 1 and items[0]["pid"] == "21" and items[0]["name"] == "LAX Basic"


def test_directory_order_button_requires_second_page_confirmation(monkeypatch):
    from app.detectors import inspect_monitor

    monkeypatch.setattr("time.sleep", lambda _: None)
    url = "https://example.com/products"
    config = Monitor(provider="VMISS", name="LAX.Basic", adapter="whmcs", url=url, expected_text="LAX.Basic")
    directory = Page(
        200,
        '<div class="product"><h3>LAX.Basic</h3><a href="/cart.php?a=add&pid=32">Order Now</a></div>',
        url,
    )
    calls = []

    def fetch(address):
        calls.append(address)
        return directory if address == url else Page(200, "<h1>Out of Stock</h1>", address)

    assert inspect_monitor(config, fetch).status == "out_of_stock"
    assert len(calls) == 2 and "pid=32" in calls[-1]


def test_directory_missing_pid_stays_unknown():
    from app.detectors import inspect_monitor

    url = "https://example.com/products"
    config = Monitor(provider="VMISS", name="LAX.Basic", adapter="whmcs", url=url, expected_text="LAX.Basic")
    directory = Page(200, '<div class="product"><h3>LAX.Basic</h3><a>Order Now</a></div>', url)
    assert inspect_monitor(config, lambda _: directory).status == "unknown"
