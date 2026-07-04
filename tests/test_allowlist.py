"""Allowlist + rate-limit tests (§7). Pure logic, no network."""

from __future__ import annotations

from atelios.webread import RateLimiter, domain_allowed, load_allowlist

ALLOW = ["fr.wikipedia.org", "wttr.in", "arxiv.org"]


def test_exact_domain_allowed():
    assert domain_allowed("https://fr.wikipedia.org/wiki/Chose", ALLOW)
    assert domain_allowed("http://wttr.in/Paris", ALLOW)


def test_subdomain_allowed():
    assert domain_allowed("https://sub.arxiv.org/abs/1234", ALLOW)


def test_off_allowlist_refused():
    assert not domain_allowed("https://example.com/", ALLOW)
    assert not domain_allowed("https://evil.org/", ALLOW)


def test_lookalike_not_allowed():
    # A domain that merely ends with the string but is not a subdomain.
    assert not domain_allowed("https://notwttr.in/", ALLOW)
    assert not domain_allowed("https://fr.wikipedia.org.evil.com/", ALLOW)


def test_non_http_scheme_refused():
    assert not domain_allowed("ftp://arxiv.org/x", ALLOW)
    assert not domain_allowed("file:///etc/passwd", ALLOW)


def test_real_allowlist_file_loads_ten():
    domains = load_allowlist()
    # §7 lists exactly 10 initial domains.
    assert len(domains) == 10
    assert "wttr.in" in domains
    assert "plato.stanford.edu" in domains


def test_rate_limiter_blocks_after_limit():
    rl = RateLimiter(per_hour=3)
    now = 1000.0
    for i in range(3):
        assert rl.allowed(now)
        rl.record(now)
    assert not rl.allowed(now)


def test_rate_limiter_window_slides():
    rl = RateLimiter(per_hour=2)
    rl.record(1000.0)
    rl.record(1001.0)
    assert not rl.allowed(1002.0)
    # More than an hour later, the old records fall out of the window.
    assert rl.allowed(1000.0 + 3601.0)
