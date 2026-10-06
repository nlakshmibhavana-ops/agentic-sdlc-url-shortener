# URL Shortener API (v1.0.0)

Generated from the application's OpenAPI schema (`openapi.json`).

| Method | Path | Summary | Responses |
|---|---|---|---|
| POST | `/api/v1/links` | Create Link | 201, 400, 401, 404, 409, 410, 422, 429 |
| DELETE | `/api/v1/links/{code}` | Delete Link | 204, 400, 401, 404, 409, 410, 422, 429 |
| GET | `/api/v1/links/{code}` | Get Link | 200, 400, 401, 404, 409, 410, 422, 429 |
| GET | `/api/v1/links/{code}/stats` | Link Stats | 200, 400, 401, 404, 409, 410, 422, 429 |
| GET | `/healthz` | Healthz | 200 |
| GET | `/readyz` | Readyz | 200 |
| GET | `/{code}` | Follow | 302, 400, 401, 404, 409, 410, 422, 429 |
