"""Minimal browser UI at "/" for trying the service. It only calls the public JSON API."""

HOME_HTML = """<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>URL Shortener</title>
<style>
  body { font-family: system-ui, sans-serif; max-width: 640px; margin: 40px auto;
         padding: 0 16px; color: #222; background: #fafafa; }
  @media (prefers-color-scheme: dark) { body { color: #eee; background: #181818; } }
  input { width: 100%; padding: 8px; margin: 4px 0 12px; box-sizing: border-box; }
  button { padding: 8px 16px; margin-right: 8px; cursor: pointer; }
  pre { background: rgba(127, 127, 127, .15); padding: 12px; overflow-x: auto; }
  label { font-size: 14px; }
  a { color: #3b82f6; }
</style>
</head>
<body>
<h1>URL Shortener</h1>
<p>Create a short link, open it, then check its click stats.
   Full API: <a href="/docs">/docs</a></p>
<label>API key <input id="key" value="dev-key"></label>
<label>Long URL <input id="url" value="https://example.com/a/very/long/path"></label>
<label>Custom alias (optional) <input id="alias" placeholder="e.g. spring-sale"></label>
<button id="create">Shorten</button>
<button id="stats">Stats for last link</button>
<p id="link"></p>
<pre id="out">Responses appear here.</pre>
<script>
  let lastCode = null;
  const $ = (id) => document.getElementById(id);
  async function call(method, path, body) {
    const resp = await fetch(path, {
      method,
      headers: {"X-API-Key": $("key").value, "Content-Type": "application/json"},
      body: body ? JSON.stringify(body) : undefined,
    });
    const data = await resp.json();
    $("out").textContent = resp.status + "\\n" + JSON.stringify(data, null, 2);
    return {ok: resp.ok, data};
  }
  $("create").onclick = async () => {
    const body = {url: $("url").value};
    if ($("alias").value) body.alias = $("alias").value;
    const {ok, data} = await call("POST", "/api/v1/links", body);
    $("link").textContent = "";
    if (ok) {
      lastCode = data.code;
      const a = document.createElement("a");
      a.href = "/" + data.code;
      a.target = "_blank";
      a.textContent = data.short_url;
      $("link").append("Short link: ", a, " (click it, then press Stats)");
    }
  };
  $("stats").onclick = () => {
    if (lastCode) call("GET", "/api/v1/links/" + encodeURIComponent(lastCode) + "/stats");
  };
</script>
</body>
</html>
"""
