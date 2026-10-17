"""NSE endpoint probe: ok / blocked / throttled / field_mismatch / error for every NSE data type,
through the same client as production (NSE_HTTP_PROXY if set)."""

import io
import time
import zipfile
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
from typing import Any

import httpx

from app.collectors import nse_http
from app.collectors.nse import BASE, HEADERS
from app.config import IST
from app.yaml_config import load_yaml

BLOCK_MARKERS = ("access denied", "akamai", "reference #", "request blocked", "captcha")


@dataclass
class ProbeResult:
    name: str
    data: str
    status: str  # ok / blocked / throttled / field_mismatch / error
    http: int | None = None
    ms: int | None = None
    missing: list[str] = field(default_factory=list)
    note: str = ""
    alternative: str = ""
    via_proxy: bool = False

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def last_trading_day(today: date) -> date:
    d = today - timedelta(days=1)
    while d.weekday() >= 5:
        d -= timedelta(days=1)
    return d


def fill(template: str, today: date) -> str:
    d = last_trading_day(today)
    return template.format(
        ddmmyyyy=d.strftime("%d%m%Y"),
        yyyymmdd=d.strftime("%Y%m%d"),
        from_dmy=(today - timedelta(days=7)).strftime("%d-%m-%Y"),
        to_dmy=today.strftime("%d-%m-%Y"),
    )


def _records(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [r for r in body if isinstance(r, dict)]
    if isinstance(body, dict):
        for k in ("data", "rows"):
            if isinstance(body.get(k), list):
                return [r for r in body[k] if isinstance(r, dict)]
        return [body]
    return []


def _has(rec: dict[str, Any], dotted: str) -> bool:
    cur: Any = rec
    for part in dotted.split("."):
        if not isinstance(cur, dict) or part not in cur:
            return False
        cur = cur[part]
    return True


def missing_fields(body: Any, fields: list[str]) -> list[str]:
    recs = _records(body)
    if not recs:
        return []  # an empty list is valid (e.g. no ban today); cannot check fields
    return [f for f in fields if not any(_has(r, f) for r in recs[:20])]


def classify(
    resp: httpx.Response | None, ms: int, slow_ms: int, err: Exception | None
) -> tuple[str, str]:
    if err is not None:
        if isinstance(err, httpx.TimeoutException):
            return "throttled", f"timeout ({type(err).__name__})"
        return "error", type(err).__name__
    assert resp is not None
    if resp.status_code in (401, 403):
        return "blocked", f"HTTP {resp.status_code}"
    if resp.status_code in (429, 503):
        return "throttled", f"HTTP {resp.status_code}"
    if resp.status_code >= 400:
        return "error", f"HTTP {resp.status_code}"
    text = resp.text[:2000].lower() if "text/html" in resp.headers.get("content-type", "") else ""
    if text and any(m in text for m in BLOCK_MARKERS):
        return "blocked", "HTML block / challenge page instead of data"
    if ms > slow_ms:
        return "throttled", f"slow ({ms} ms)"
    return "ok", ""


def probe_one(ep: dict[str, Any], c: httpx.Client, today: date, slow_ms: int) -> ProbeResult:
    res = ProbeResult(
        ep["name"],
        ep["data"],
        "error",
        alternative=ep.get("alternative", ""),
        via_proxy=bool(nse_http.proxy()),
    )
    url = f"{BASE}{fill(ep['json_path'], today)}" if "json_path" in ep else fill(ep["url"], today)
    t0 = time.monotonic()
    resp, err = None, None
    try:
        resp = c.get(url)
    except httpx.HTTPError as exc:
        err = exc
    res.ms = int((time.monotonic() - t0) * 1000)
    res.http = resp.status_code if resp is not None else None
    res.status, res.note = classify(resp, res.ms, slow_ms, err)
    if res.status not in ("ok", "throttled") or resp is None:
        return res
    try:
        if "json_path" in ep:
            res.missing = missing_fields(resp.json(), list(ep.get("fields") or []))
        elif ep.get("zip"):
            with zipfile.ZipFile(io.BytesIO(resp.content)) as z:
                head = z.read(z.namelist()[0])[:2000].decode("utf-8", "ignore")
            res.note = res.note or f"zip ok: {head.splitlines()[0][:80]}"
        else:
            head = resp.text.splitlines()[0] if resp.text else ""
            res.missing = [
                x for x in ep.get("csv_contains", []) if x.lower() not in resp.text[:5000].lower()
            ]
            res.note = res.note or head[:80]
    except (ValueError, zipfile.BadZipFile, IndexError) as exc:
        res.status, res.note = (
            "blocked",
            f"not the expected format ({type(exc).__name__}): likely a block page",
        )
        return res
    if res.missing and res.status == "ok":
        res.status = "field_mismatch"
    return res


def probe_all(now: datetime | None = None, client: httpx.Client | None = None) -> list[ProbeResult]:
    cfg = load_yaml("golive.yaml")["nse_probe"]
    today = (now or datetime.now(IST)).date()
    c = client or nse_http.client(
        headers=HEADERS, timeout=float(cfg["timeout_s"]), follow_redirects=True
    )
    try:
        try:
            c.get(BASE)  # cookie warm-up, like production
        except httpx.HTTPError:
            pass
        return [probe_one(ep, c, today, int(cfg["slow_ms"])) for ep in cfg["endpoints"]]
    finally:
        if client is None:
            c.close()
