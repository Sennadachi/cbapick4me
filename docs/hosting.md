# Hosting the web version

The GUI can also run as a website, so people can use it without installing anything.

```bash
git clone https://github.com/Sennadachi/cbapick4me && cd cbapick4me
cp .env.example .env    # add the server's DigiKey keys and a CBAPICK_STORAGE_SECRET
docker compose -f packaging/docker-compose.yml up -d --build
```

Without Docker, run `cbapick4me --serve` (`--host`, `--port`).

- Every browser tab has its own isolated state. BOMs are parsed in memory and never written to disk. Results are sent back as a browser download.
- Users can enter their own API keys. Those keys live only in that tab's memory and are never saved on the server.
- The server's own keys sit in `.env` on the server. Keep that file out of git and readable only by the service user.
- The search cache is shared between users (`/data` volume), so common parts cost one API call per day.
- Limits:
  - upload size: 1 MB
  - BOM rows: `CBAPICK_MAX_ROWS` (500)
  - pick runs per IP per hour: `CBAPICK_RATE_PER_HOUR` (20)
- `GET /healthz` is the health check.
- Put it behind a TLS reverse proxy that forwards **websockets**, which NiceGUI needs. For example, Caddy:

  ```
  bom.example.com {
      reverse_proxy 127.0.0.1:8080
  }
  ```

  If you use nginx, add `proxy_http_version 1.1; proxy_set_header Upgrade $http_upgrade; proxy_set_header Connection "upgrade";`.
