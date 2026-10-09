#!/bin/sh
set -eu
# psql reads SQL on stdin. No passwords appear in process arguments or checked-in files.
forge_app_password=$(cat /run/secrets/postgres_app)
export forge_app_password
psql --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<'SQL'
\getenv forge_app_password forge_app_password
CREATE ROLE forge LOGIN PASSWORD :'forge_app_password' NOSUPERUSER NOBYPASSRLS;
ALTER DATABASE forge OWNER TO forge;
ALTER SCHEMA public OWNER TO forge;
SQL
unset forge_app_password
