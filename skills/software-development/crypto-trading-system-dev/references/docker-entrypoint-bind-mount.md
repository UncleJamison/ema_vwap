# Docker entrypoint with bind-mounted entrypoint.sh

Pitfall: A bind-mounted `./entrypoint.sh` (e.g., `docker-compose.yml` volume `- ./entrypoint.sh:/app/entrypoint.sh`) carries Windows CRLF (`\r\n`) and no Linux executable bit, so Linux `exec /app/entrypoint.sh` fails with `no such file or directory` (or `bad trap`).

Fix (imperative):
1. Convert `entrypoint.sh` to LF (`\r\n` → `\n`).
2. Replace absolute `.env` paths (`/Users/Josh/.env`) with relative `.env` so the container finds them regardless of mount.
3. Change Dockerfile `ENTRYPOINT` to use `/bin/sh`: `ENTRYPOINT ["/bin/sh", "/app/entrypoint.sh"]` — this avoids the executable-permission requirement and works with any bind-mounted file.
4. For trap syntax: use numeric signals (`trap 'cleanup' 15 2`) because POSIX `/bin/sh` does not always accept `SIGTERM`/`SIGINT` strings.
5. Rebuild with `--no-cache` and restart (`docker-compose up -d --force-recreate`). Verify startup banner and `/api/paper/engine/status` returns 200.

Verification checklist:
- `docker-compose build --no-cache`
- `docker-compose up -d --force-recreate`
- Confirm container starts: `docker ps` shows running; logs show `EMA + VWAP Crypto Trading Suite Daemon Started`
- Confirm web endpoints respond: `curl -f http://localhost:8080/`
