import socket

import pytest

from app.fetcher import fetch_page, resolve_public
from app.security import InstanceLock, Vault


@pytest.mark.parametrize(
    "address",
    [
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "192.168.1.1",
        "169.254.169.254",
        "::1",
        "fc00::1",
        "0.0.0.0",
        "224.0.0.1",
    ],
)
def test_private_and_reserved_addresses_are_blocked(monkeypatch, address):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", (address, 443))]
    )
    with pytest.raises(ValueError):
        resolve_public("example.com", 443)


def test_mixed_public_private_dns_is_blocked(monkeypatch):
    monkeypatch.setattr(
        socket, "getaddrinfo", lambda *a, **k: [(2, 1, 6, "", (ip, 443)) for ip in ["1.1.1.1", "127.0.0.1"]]
    )
    with pytest.raises(ValueError):
        resolve_public("example.com", 443)


def test_connection_is_pinned_and_tls_hostname_preserved(monkeypatch):
    monkeypatch.setattr("app.fetcher.resolve_public", lambda *a: ["1.1.1.1"])
    seen = {}

    class Response:
        status = 200
        headers = {"Content-Type": "text/html"}

        def stream(self, *args, **kwargs):
            yield b"<h1>Hello</h1>"

        def close(self):
            pass

    class Pool:
        def __init__(self, **kwargs):
            seen.update(kwargs)

        def urlopen(self, *args, **kwargs):
            seen["headers"] = kwargs["headers"]
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("app.fetcher.urllib3.HTTPSConnectionPool", Pool)
    assert fetch_page("https://example.com/stock").status == 200
    assert (
        seen["host"] == "1.1.1.1"
        and seen["server_hostname"] == "example.com"
        and seen["assert_hostname"] == "example.com"
    )
    assert seen["headers"]["Host"] == "example.com"


def test_redirect_to_other_domain_is_not_followed(monkeypatch):
    monkeypatch.setattr("app.fetcher.resolve_public", lambda *a: ["1.1.1.1"])

    class Response:
        status = 302
        headers = {"Location": "http://169.254.169.254/latest/meta-data/"}

        def close(self):
            pass

    class Pool:
        def __init__(self, **kwargs):
            pass

        def urlopen(self, *args, **kwargs):
            return Response()

        def close(self):
            pass

    monkeypatch.setattr("app.fetcher.urllib3.HTTPSConnectionPool", Pool)
    with pytest.raises(ValueError):
        fetch_page("https://example.com")


def test_vault_reopens_and_instance_lock_prevents_duplicate_workers(tmp_path):
    first = Vault(tmp_path)
    secret = first.encrypt("smtp-secret")
    assert Vault(tmp_path).decrypt(secret) == "smtp-secret"
    lock = InstanceLock(tmp_path)
    try:
        with pytest.raises(RuntimeError):
            InstanceLock(tmp_path)
    finally:
        lock.close()
