import ipaddress
import socket
import time
from dataclasses import dataclass
from urllib.parse import urljoin, urlsplit

import urllib3

from app.models import public_url

MAX_BODY = 2 * 1024 * 1024


@dataclass
class Page:
    status: int
    text: str
    url: str
    latency: int = 0
    content_type: str = "text/html"


def resolve_public(host, port):
    addresses = list(
        dict.fromkeys(item[4][0] for item in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM))
    )
    if not addresses or any(
        (
            not ipaddress.ip_address(ip).is_global
            or ipaddress.ip_address(ip).is_multicast
            or ipaddress.ip_address(ip).is_reserved
        )
        for ip in addresses
    ):
        raise ValueError("检测地址不能指向本机、内网、保留地址或云元数据服务")
    return addresses


def fetch_page(url):
    """Pin the validated address to the TCP connection, including TLS SNI verification.

    Do not use environment proxies/cookies; revalidate every redirect to prevent SSRF.
    """
    started = time.monotonic()
    original_host = urlsplit(url).hostname
    for _ in range(5):
        public_url(url)
        parsed = urlsplit(url)
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
        address = resolve_public(parsed.hostname, port)[0]
        kwargs = dict(host=address, port=port, timeout=urllib3.Timeout(connect=8, read=12), maxsize=1)
        if parsed.scheme == "https":
            pool = urllib3.HTTPSConnectionPool(
                **kwargs,
                assert_hostname=parsed.hostname,
                server_hostname=parsed.hostname,
                cert_reqs="CERT_REQUIRED",
            )
        else:
            pool = urllib3.HTTPConnectionPool(**kwargs)
        response = None
        try:
            target = parsed.path or "/"
            if parsed.query:
                target += "?" + parsed.query
            response = pool.urlopen(
                "GET",
                target,
                redirect=False,
                retries=False,
                preload_content=False,
                headers={
                    "Host": parsed.netloc,
                    "User-Agent": "VPSRadar/1.0 (+self-hosted inventory monitor)",
                    "Accept": "text/html,application/json",
                    "Accept-Encoding": "identity",
                },
            )
            if response.status in {301, 302, 303, 307, 308}:
                next_url = urljoin(url, response.headers.get("Location", ""))
                # A rebrand/login redirect is not evidence about the original product.
                if urlsplit(next_url).hostname != original_host:
                    raise ValueError("检测页面跳转到了其他域名，请核实并更新地址")
                if parsed.scheme == "https" and urlsplit(next_url).scheme != "https":
                    raise ValueError("拒绝 HTTPS 降级跳转")
                url = next_url
                continue
            chunks = []
            size = 0
            for chunk in response.stream(65536, decode_content=True):
                size += len(chunk)
                if size > MAX_BODY or time.monotonic() - started > 35:
                    raise ValueError("页面过大或响应超时")
                chunks.append(chunk)
            body = b"".join(chunks).decode("utf-8", errors="replace")
            return Page(
                response.status,
                body,
                url,
                int((time.monotonic() - started) * 1000),
                response.headers.get("Content-Type", ""),
            )
        finally:
            if response:
                response.close()
            pool.close()
    raise ValueError("页面重定向次数过多")
