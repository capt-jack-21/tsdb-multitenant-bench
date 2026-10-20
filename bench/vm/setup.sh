#!/usr/bin/env bash
# One-time setup on a fresh Ubuntu 24.04 VM. Run from the repo root:  bash bench/vm/setup.sh
# Installs Docker, pgbench and Python, writes the PostgreSQL settings for a 16 GB / 4 vCPU box,
# starts TimescaleDB, and records the exact image digest.
set -euo pipefail

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y docker.io docker-compose-v2 postgresql-contrib python3-venv python3-pip tmux sysstat git

# The postgresql package starts its own server on 5432; we only want the pgbench client.
systemctl disable --now postgresql 2>/dev/null || true
systemctl enable --now docker

python3 -m venv .venv
.venv/bin/pip install -q -r bench/requirements.txt

# Settings for 16 GB RAM. Recorded in results/environment.json by every run.
cat > .env <<'ENV'
POSTGRES_PASSWORD=postgres
PG_SHARED_BUFFERS=4GB
PG_EFFECTIVE_CACHE_SIZE=12GB
PG_WORK_MEM=32MB
PG_MAINT_WORK_MEM=1GB
PG_MAX_WAL_SIZE=8GB
ENV

docker compose pull
docker compose up -d --wait

mkdir -p results/official
{
  echo "image: $(docker image inspect --format '{{index .RepoDigests 0}}' timescale/timescaledb:latest-pg16)"
  echo "kernel: $(uname -srm)"
  echo "os: $(. /etc/os-release && echo "$PRETTY_NAME")"
  echo "pgbench: $(pgbench --version)"
  echo "python: $(.venv/bin/python --version)"
  echo "docker: $(docker --version)"
  lscpu | grep -E 'Model name|^CPU\(s\)|Thread|Core|MHz' || true
  free -h | head -2
  df -h / | tail -1
  echo "commit: $(git rev-parse HEAD)"
} | tee results/official/vm_environment.txt

echo
echo "Setup done. Next: tmux new -s bench, then  bash bench/vm/run_all.sh"
