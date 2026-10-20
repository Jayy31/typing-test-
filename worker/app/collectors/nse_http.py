"""One place for every NSE request (website JSON, archives, bhavcopy downloads).

NSE often blocks or throttles data-centre IPs. If NSE_HTTP_PROXY is set (e.g. a residential or
Indian-hosted proxy), it is used for NSE hosts ONLY; every other request goes direct.
"""

from typing import Any

import httpx

from app.config import get_settings

NSE_HOSTS = ("nseindia.com", "niftyindices.com")  # www., archives., nsearchives.; NSE Indices


def is_nse(url: str) -> bool:
    host = httpx.URL(url).host
    return any(host == h or host.endswith("." + h) for h in NSE_HOSTS)


def proxy() -> str | None:
    return get_settings().nse_http_proxy or None


def client(**kw: Any) -> httpx.Client:
    """httpx client for NSE requests (proxy if configured)."""
    p = proxy()
    return httpx.Client(proxy=p, **kw) if p else httpx.Client(**kw)


def get(url: str, **kw: Any) -> httpx.Response:
    if not is_nse(url):
        return httpx.get(url, **kw)
    with client(
        timeout=kw.pop("timeout", 20.0), follow_redirects=kw.pop("follow_redirects", True)
    ) as c:
        return c.get(url, **kw)
