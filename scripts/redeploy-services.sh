#!/bin/sh
set -eu

if [ "$#" -eq 0 ]; then
    echo "Usage: $0 SERVICE [SERVICE ...]" >&2
    exit 2
fi

script_directory=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
repository_directory=$(dirname -- "$script_directory")
cd "$repository_directory"

docker compose build --pull "$@"
docker compose up -d --force-recreate "$@"
docker compose ps "$@"
