#!/usr/bin/env bash
# Run on a disposable Linux runner with runsc and the repository's sandbox image.
set -euo pipefail
state=$(mktemp -d "${RUNNER_TEMP:-/tmp}/forge-compose.XXXXXXXX")
export COMPOSE_PROJECT_NAME="forge-smoke-${state##*.}"
COMPOSE_PROJECT_NAME=${COMPOSE_PROJECT_NAME,,}
export FORGE_SHARED_DATA="$state/data" FORGE_TLS_DIR="$state/tls"
export FORGE_MANAGER_ENV_FILE="$state/manager.env" FORGE_DEPLOY_ENV_FILE="$state/worker.env"
export FORGE_POSTGRES_ADMIN_FILE="$state/admin" FORGE_POSTGRES_APP_FILE="$state/app"
export FORGE_RUNTIME_IMAGE="$COMPOSE_PROJECT_NAME:runtime" FORGE_MANAGER_IMAGE="$COMPOSE_PROJECT_NAME:manager"
export FORGE_WEB_IMAGE="$FORGE_RUNTIME_IMAGE" # web is not started in this gate
FORGE_DOCKER_GID=$(stat -c %g /var/run/docker.sock)
export FORGE_DOCKER_GID
compose=(docker compose -f compose.deploy.yaml)
cleanup() {
  result=$?
  if ((result)); then "${compose[@]}" logs --tail=60 sandbox-manager postgres || true; fi
  "${compose[@]}" down -v --remove-orphans || true
  sudo rm -rf -- "$state"
}
trap cleanup EXIT
mkdir "$FORGE_TLS_DIR"
sudo install -d -o 10001 -g 10001 -m 755 "$FORGE_SHARED_DATA"
bash scripts/manager-test-tls.sh "$FORGE_TLS_DIR"
sudo chown 10001:10001 "$FORGE_TLS_DIR/manager.key"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=UnrelatedTestCA \
  -addext basicConstraints=critical,CA:TRUE -keyout "$state/unrelated.key" -out "$state/unrelated.crt"
printf '%s\n' "FORGE_SANDBOX_MANAGER_SECRET=$(openssl rand -hex 32)" > "$FORGE_MANAGER_ENV_FILE"
cp "$FORGE_MANAGER_ENV_FILE" "$FORGE_DEPLOY_ENV_FILE"
openssl rand -hex 24 > "$FORGE_POSTGRES_ADMIN_FILE"
openssl rand -hex 24 > "$FORGE_POSTGRES_APP_FILE"
printf 'FORGE_DATABASE_URL=postgresql+psycopg://forge:%s@postgres:5432/forge\n' \
  "$(cat "$FORGE_POSTGRES_APP_FILE")" >> "$FORGE_DEPLOY_ENV_FILE"
chmod 644 "$FORGE_POSTGRES_APP_FILE" "$FORGE_POSTGRES_ADMIN_FILE" "$state/unrelated.crt"
docker build --target runtime -t "$FORGE_RUNTIME_IMAGE" .
docker build --target manager -t "$FORGE_MANAGER_IMAGE" .
"${compose[@]}" up -d --wait --wait-timeout 90 postgres sandbox-manager
"${compose[@]}" exec -T sandbox-manager python -c \
  'import os; assert not {"FORGE_DATABASE_URL","FORGE_MODEL_API_KEY","FORGE_S3_SECRET_KEY"}.intersection(os.environ)'
"${compose[@]}" run --rm --no-deps -T -v "$state/unrelated.crt:/tmp/unrelated.crt:ro" worker python - <<'PYTHON'
import asyncio
import os
from pathlib import Path
import httpx
from forgeagent.config import settings
from forgeagent.domain import uid
from forgeagent.sandbox import sandbox
from forgeagent.sandbox_manager import manager_client

async def main():
    url = settings.sandbox_manager_url + "/openapi.json"
    async with manager_client(5) as client:
        for attempt in range(30):
            try:
                (await client.get(url)).raise_for_status()
                break
            except httpx.ConnectError:
                if attempt == 29:
                    raise
                await asyncio.sleep(1)
    image = await sandbox.image_digest("forgeagent-sandbox:local")
    workspace = sandbox.root("ci-smoke", uid(), 1)
    workspace.mkdir(parents=True)
    marker = uid()
    (workspace / "marker").write_text(marker)
    # statvfs reads the actual mount flags from inside the sandbox.
    argv = ["python", "-c", "import os; from pathlib import Path; "
            "assert os.getuid() == 10001 and os.statvfs('.').f_flag & os.ST_RDONLY; "
            "print(Path('marker').read_text())"]
    result = await sandbox.execute(workspace, argv, image, timeout=45, readonly=True)
    assert result["exit_code"] == 0, result["output"]
    assert result["output"].strip() == marker and result["sandbox_profile"]["runtime"] == "runsc"
    settings.sandbox_manager_ca_file = Path("/tmp/unrelated.crt")
    try:
        await sandbox.image_digest("forgeagent-sandbox:local")
    except httpx.ConnectError as exc:
        assert "CERTIFICATE_VERIFY_FAILED" in str(exc), str(exc)
    else:
        raise AssertionError("An unrelated CA authenticated the manager")
    print("Compose worker verified a read-only marker via runsc and rejected an unrelated CA.")

asyncio.run(main())
PYTHON
