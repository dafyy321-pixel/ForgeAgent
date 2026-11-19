#!/usr/bin/env bash
set -euo pipefail
umask 077
cd "${1:?supply a private temporary TLS directory}"
openssl req -x509 -newkey rsa:2048 -nodes -days 1 -subj /CN=ForgeTestCA \
  -addext basicConstraints=critical,CA:TRUE -keyout ca.key -out ca.crt
openssl req -new -newkey rsa:2048 -nodes -subj /CN=sandbox-manager -keyout manager.key -out manager.csr
printf '%s\n' 'subjectAltName=DNS:sandbox-manager,IP:127.0.0.1' 'extendedKeyUsage=serverAuth' > manager.ext
openssl x509 -req -in manager.csr -CA ca.crt -CAkey ca.key -CAcreateserial \
  -days 1 -extfile manager.ext -out manager.crt
chmod 644 ca.crt manager.crt
