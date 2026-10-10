#!/bin/sh
# Read-only, narrowly scoped inventory: no Env, passwords, key files or database dump.
set -eu
docker version --format '{{.Server.Version}}'
docker compose version
docker inspect blog-frontend --format '{{json .Mounts}}'
docker inspect blog-frontend --format '{{json .NetworkSettings.Networks}}'
docker inspect blog-frontend --format '{{index .Config.Labels "com.docker.compose.project.working_dir"}} {{index .Config.Labels "com.docker.compose.project.config_files"}}'
ss -ltnp | awk '$4 ~ /:(80|443)$/ {print}'
docker exec blog-frontend nginx -t
