"""webread — allowlisted GET fetch with rate limit (§7).

The world answers honestly (invariant 2): a URL off the allowlist gets an
explicit refusal, an exhausted rate limit gets an honest refusal — never a
simulated success. Every fetch and every refusal is written to the events audit
table (§7).

The allowlist match (domain_allowed) and the rate-limit accounting are pure
functions, tested without network. The actual fetch uses httpx + selectolax.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import urlparse

import httpx
from selectolax.parser import HTMLParser

from . import config

HTTP_TIMEOUT_S = 15
REFUSAL_OFF_ALLOWLIST = "cette adresse n'est pas dans ta fenêtre sur le monde"
REFUSAL_RATE_LIMIT = "ta fenêtre sur le monde est épuisée pour cette heure"


def load_allowlist(path: Path | str | None = None) -> list[str]:
    """Read allowlist.txt: one domain per line, blanks and # comments ignored."""
    p = Path(path) if path is not None else config.ALLOWLIST_PATH
    domains: list[str] = []
    for raw in p.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line and not line.startswith("#"):
            domains.append(line.lower())
    return domains


def domain_allowed(url: str, allowlist: list[str]) -> bool:
    """True if the URL host matches an allowlist entry exactly or as a subdomain.

    Pure function (§7: exact domain or subdomain match). Scheme must be http(s).
    """
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return False
    host = (parsed.hostname or "").lower()
    if not host:
        return False
    for entry in allowlist:
        if host == entry or host.endswith("." + entry):
            return True
    return False


@dataclass
class RateLimiter:
    """Sliding-window rate limiter over the last hour. Pure accounting."""

    per_hour: int
    _timestamps: list[float] = field(default_factory=list)

    def _prune(self, now: float) -> None:
        cutoff = now - 3600.0
        self._timestamps = [t for t in self._timestamps if t > cutoff]

    def allowed(self, now: float | None = None) -> bool:
        now = time.time() if now is None else now
        self._prune(now)
        return len(self._timestamps) < self.per_hour

    def record(self, now: float | None = None) -> None:
        now = time.time() if now is None else now
        self._timestamps.append(now)

    @property
    def count(self) -> int:
        return len(self._timestamps)


def extract_text(html: str) -> str:
    """Extract visible text via selectolax, collapse whitespace."""
    tree = HTMLParser(html)
    body = tree.body if tree.body is not None else tree.root
    if body is None:
        return ""
    text = body.text(separator=" ", strip=True)
    return " ".join(text.split())


@dataclass
class FetchResult:
    ok: bool
    text: str          # extracted text on success, or the honest refusal/error
    status: int | None
    refused: bool      # True when refused by allowlist or rate limit


class WebReader:
    """Stateful web reader: holds the allowlist, rate limiter, and events sink."""

    def __init__(self, conn, allowlist: list[str] | None = None,
                 per_hour: int | None = None):
        from . import db  # local import to avoid cycle at module load

        self._db = db
        self._conn = conn
        self._allowlist = allowlist if allowlist is not None else load_allowlist()
        self._rl = RateLimiter(
            per_hour if per_hour is not None else config.WEB_RATE_PER_HOUR
        )

    def read(self, url: str) -> FetchResult:
        if not domain_allowed(url, self._allowlist):
            self._db.log_event(self._conn, "web_refused",
                               {"url": url, "reason": "off_allowlist"})
            return FetchResult(False, REFUSAL_OFF_ALLOWLIST, None, refused=True)

        if not self._rl.allowed():
            self._db.log_event(self._conn, "web_refused",
                               {"url": url, "reason": "rate_limit"})
            return FetchResult(False, REFUSAL_RATE_LIMIT, None, refused=True)

        self._rl.record()
        try:
            # No redirect off the allowlist: follow_redirects off, re-check any
            # Location the caller might follow manually stays honest.
            resp = httpx.get(url, timeout=HTTP_TIMEOUT_S, follow_redirects=False)
        except httpx.HTTPError as exc:
            # Real error, verbatim (invariant 2).
            self._db.log_event(self._conn, "web_error",
                               {"url": url, "error": str(exc)})
            return FetchResult(False, str(exc), None, refused=False)

        text = extract_text(resp.text)[: config.WEB_MAX_CHARS]
        self._db.log_event(self._conn, "web_fetch",
                           {"url": url, "status": resp.status_code,
                            "chars": len(text)})
        return FetchResult(True, text, resp.status_code, refused=False)
