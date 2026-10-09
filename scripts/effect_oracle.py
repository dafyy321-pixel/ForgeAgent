"""Independent test effect oracle: SQLite commits business keys before replying.

No ForgeAgent imports, database, events or receipts are used to compute counts.
Run only against a dedicated test database; the service has no reset endpoint.
"""

import argparse
import hashlib
import hmac
import json
import os
import socket
import sqlite3
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def server(database, token, host="127.0.0.1", port=0):
    if not token:
        raise ValueError("A private oracle token is required")
    with sqlite3.connect(database) as connection:
        connection.execute("CREATE TABLE IF NOT EXISTS effects (scope TEXT, key TEXT, digest TEXT, PRIMARY KEY(scope,key))")
        connection.execute("CREATE TABLE IF NOT EXISTS attempts (scope TEXT, key TEXT, digest TEXT)")
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, *args):
            pass

        def respond(self, status, value):
            body = json.dumps(value).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def authorized(self):
            return hmac.compare_digest(self.headers.get("Authorization", ""), "Bearer " + token)

        def do_GET(self):
            if not self.authorized():
                self.respond(401, {"code": "UNAUTHORIZED"})
                return
            scope = self.headers.get("X-Test-Scope", "")
            with sqlite3.connect(database) as connection:
                rows = connection.execute("SELECT key,digest FROM effects WHERE scope=? ORDER BY key", (scope,)).fetchall()
                attempts = connection.execute("SELECT COUNT(*) FROM attempts WHERE scope=?", (scope,)).fetchone()[0]
            self.respond(200, {"count": len(rows), "attempts": attempts, "effects": [{"key": key, "digest": checksum} for key, checksum in rows]})

        def do_POST(self):
            if not self.authorized():
                self.respond(401, {"code": "UNAUTHORIZED"})
                return
            length = int(self.headers.get("Content-Length", "0"))
            scope, key = self.headers.get("X-Test-Scope", ""), self.headers.get("Idempotency-Key", "")
            if not scope or not key or not 0 < length <= 65536:
                self.respond(422, {"code": "INVALID_EFFECT"})
                return
            checksum = hashlib.sha256(self.rfile.read(length)).hexdigest()
            with sqlite3.connect(database, timeout=10) as connection:
                connection.execute("BEGIN IMMEDIATE")
                prior = connection.execute("SELECT digest FROM effects WHERE scope=? AND key=?", (scope, key)).fetchone()
                if prior and prior[0] != checksum:
                    self.respond(409, {"code": "KEY_CONFLICT"})
                    return
                connection.execute("INSERT OR IGNORE INTO effects VALUES (?,?,?)", (scope, key, checksum))
                connection.execute("INSERT INTO attempts VALUES (?,?,?)", (scope, key, checksum))
            if self.headers.get("X-Test-Drop-After-Commit") == "1":
                self.connection.shutdown(socket.SHUT_RDWR)
                self.connection.close()
                return
            self.respond(200, {"key": key, "digest": checksum, "committed": True})
    return ThreadingHTTPServer((host, port), Handler)


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--database", required=True)
    parser.add_argument("--port", type=int, default=8099)
    args = parser.parse_args()
    server(args.database, os.environ["FORGE_ORACLE_TOKEN"], port=args.port).serve_forever()
