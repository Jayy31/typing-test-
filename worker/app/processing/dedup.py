"""URL canonicalisation, hashing and near-duplicate title similarity."""

import hashlib
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

_TRACKING = {
    "utm_source",
    "utm_medium",
    "utm_campaign",
    "utm_term",
    "utm_content",
    "fbclid",
    "gclid",
}
_STOP = {
    "the",
    "a",
    "an",
    "of",
    "in",
    "on",
    "to",
    "for",
    "and",
    "as",
    "at",
    "by",
    "is",
    "are",
    "with",
    "from",
    "after",
    "over",
    "says",
    "say",
    "its",
    "it",
}


def canonical_url(url: str) -> str:
    parts = urlsplit(url.strip())
    query = urlencode([(k, v) for k, v in parse_qsl(parts.query) if k.lower() not in _TRACKING])
    return urlunsplit(
        (parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/"), query, "")
    )


def url_hash(url: str) -> str:
    return hashlib.sha256(canonical_url(url).encode()).hexdigest()


def title_tokens(title: str) -> frozenset[str]:
    words = re.findall(r"[a-z0-9&]+", title.lower())
    return frozenset(w for w in words if w not in _STOP and len(w) > 1)


def similarity(a: frozenset[str], b: frozenset[str]) -> float:
    """Jaccard similarity of two token sets."""
    if not a or not b:
        return 0.0
    return len(a & b) / len(a | b)
