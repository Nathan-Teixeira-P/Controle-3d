#!/bin/sh
docker volume create controle_pgdata >/dev/null && docker volume create controle_uploads >/dev/null && docker compose up -d --build && echo "Sistema em http://localhost:3000"
