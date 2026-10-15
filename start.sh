#!/bin/sh
# Start DFIR. Needs Docker (https://docs.docker.com/get-docker/).
cd "$(dirname "$0")"
docker info >/dev/null 2>&1 || { echo "Docker is not running. Install/start Docker, then run ./start.sh again."; exit 1; }
echo "Starting DFIR. The first start builds the app and downloads about 3 GB of models - please wait."
echo "Then open http://localhost:8000"
exec docker compose up --build
