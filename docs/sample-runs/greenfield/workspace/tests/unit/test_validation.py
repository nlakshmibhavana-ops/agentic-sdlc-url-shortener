import pytest

from shortener.validation import InvalidURL, validate_target_url


def check(url, **kw):
    return validate_target_url(url, max_length=2048, base_url="http://sho.rt", **kw)


def test_normalises_scheme_and_host_but_keeps_path_case():
    assert check("HTTPS://Example.COM/Path?q=A#Frag") == "https://example.com/Path?q=A#Frag"


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "data:text/html,<script>alert(1)</script>",
        "ftp://example.com/file",
        "file:///etc/passwd",
        "https://",
        "https://user:pass@example.com/",
        "https://paypal.com@evil.example/",
        "http://localhost:8080/admin",
        "http://127.0.0.1/",
        "http://10.0.0.5/",
        "http://192.168.1.1/",
        "http://169.254.169.254/latest/meta-data/",
        "http://[::1]/",
        "http://db.internal/",
        "http://sho.rt/abc",
        "https://example.com/has space",
        "https://example.com/\nheader",
        "",
    ],
)
def test_rejects_unsafe_urls(url):
    with pytest.raises(InvalidURL):
        check(url)


def test_rejects_overlong_urls():
    with pytest.raises(InvalidURL):
        validate_target_url("https://e.com/" + "a" * 50, max_length=40, base_url="http://sho.rt")


def test_private_hosts_allowed_when_configured():
    assert check("http://10.0.0.5/x", allow_private=True) == "http://10.0.0.5/x"


def test_keeps_explicit_port_and_ipv6():
    assert check("https://example.com:8443/a") == "https://example.com:8443/a"
    assert check("http://[2606:4700::1111]/") == "http://[2606:4700::1111]/"
