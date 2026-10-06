"""LINK-151: click counts were under-reported (lost updates + cached 301 redirects)."""

from concurrent.futures import ThreadPoolExecutor

from linkly import store


def test_concurrent_hits_are_not_lost(db_path):
    store.init_db(db_path)
    code = store.create(db_path, "https://example.com")
    with ThreadPoolExecutor(max_workers=8) as pool:
        list(pool.map(lambda _: store.hit(db_path, code), range(200)))
    assert store.lookup(db_path, code)["clicks"] == 200


def test_redirect_is_not_permanently_cacheable(client):
    code = client.post("/shorten", json={"url": "https://example.com"}).json()["code"]
    resp = client.get(f"/r/{code}")
    assert resp.status_code == 302
    assert "no-store" in resp.headers["cache-control"]
