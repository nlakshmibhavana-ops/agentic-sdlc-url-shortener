"""LINK-142: unsafe target URLs must be rejected with 400 and never stored."""

import pytest

from linkly.validation import InvalidURL, validate_url


@pytest.mark.parametrize(
    "url",
    [
        "javascript:alert(1)",
        "JAVASCRIPT:alert(1)",
        "data:text/html;base64,PHNjcmlwdD4=",
        "vbscript:msgbox(1)",
        "file:///etc/passwd",
        "//evil.example/path",
        "https://",
        "https://user:pw@bank.example/",
        "https://example.com/a b",
        "",
    ],
)
def test_unsafe_urls_rejected(url):
    with pytest.raises(InvalidURL):
        validate_url(url)


def test_valid_urls_unchanged():
    assert validate_url("https://Example.com/Path?q=1") == "https://Example.com/Path?q=1"


def test_api_rejects_script_url_and_stores_nothing(client):
    resp = client.post("/shorten", json={"url": "javascript:alert(document.cookie)"})
    assert resp.status_code == 400
    # Codes are sequential in this legacy service: nothing must have been inserted.
    ok = client.post("/shorten", json={"url": "https://example.com"}).json()["code"]
    assert ok == "1"
