#!/bin/sh
set -eu

# Compose provides a comma-separated list; convert it before shell iteration.
for database in $(echo "$POSTGRES_MULTIPLE_DATABASES" | tr ',' ' '); do
  psql -v ON_ERROR_STOP=1 --username "$POSTGRES_USER" --dbname postgres <<-EOSQL
    CREATE DATABASE "$database";
EOSQL
done
