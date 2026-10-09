#!/bin/sh
set -eu
for attempt in $(seq 1 60); do
  if mc ready forge >/dev/null 2>&1; then break; fi
  sleep 1
done
mc mb --ignore-existing forge/forgeagent
mc version enable forge/forgeagent
mc ilm rule add --abort-incomplete-multipart-upload-days 7 forge/forgeagent
mc admin policy create forge forgeagent /policy.json
mc admin user add forge "$FORGE_S3_ACCESS_KEY" "$FORGE_S3_SECRET_KEY" >/dev/null
mc admin policy attach forge forgeagent --user "$FORGE_S3_ACCESS_KEY"
