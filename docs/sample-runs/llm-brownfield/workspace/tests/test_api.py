def test_shorten_and_redirect(client):
    code = client.post("/shorten", json={"url": "https://example.com"}).json()["code"]
    resp = client.get(f"/r/{code}")
    assert resp.status_code in (301, 302)
    assert resp.headers["location"] == "https://example.com"


def test_info_counts_clicks(client):
    code = client.post("/shorten", json={"url": "https://example.com"}).json()["code"]
    client.get(f"/r/{code}")
    client.get(f"/r/{code}")
    assert client.get(f"/info/{code}").json()["clicks"] == 2


def test_unknown_code(client):
    assert client.get("/r/zzz").status_code == 404
