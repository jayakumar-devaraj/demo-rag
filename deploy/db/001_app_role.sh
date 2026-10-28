#!/bin/sh
# Runs once on first database start. Creates the non-owner role the API connects as; the schema
# itself is created by the migrate job, never by the API.
set -eu
psql -v ON_ERROR_STOP=1 -v app_user="$APP_DB_USER" -v app_pw="$APP_DB_PASSWORD" \
  -U "$POSTGRES_USER" -d "$POSTGRES_DB" <<'SQL'
SELECT format('CREATE ROLE %I LOGIN PASSWORD %L NOSUPERUSER NOCREATEDB NOCREATEROLE NOBYPASSRLS',
              :'app_user', :'app_pw') \gexec
SQL
