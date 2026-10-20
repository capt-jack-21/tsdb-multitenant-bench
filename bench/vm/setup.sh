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

# PostgreSQL settings scaled to the machine's RAM. Recorded in results/environment.json by every run.
MEM_GB=$(awk '/MemTotal/ {printf "%d", $2/1024/1024}' /proc/meminfo)
if [ "$MEM_GB" -ge 14 ]; then      # 16 GB class
  SB=4GB; EC=12GB; MW=1GB; WAL=8GB
else                               # 8 GB class: the 20M-row dataset (about 4 GB) must still fit in cache
  SB=2GB; EC=5GB; MW=512MB; WAL=4GB
fi
echo "RAM ${MEM_GB} GB -> shared_buffers=$SB effective_cache_size=$EC"
cat > .env <<ENV
POSTGRES_PASSWORD=postgres
PG_SHARED_BUFFERS=$SB
PG_EFFECTIVE_CACHE_SIZE=$EC
PG_WORK_MEM=32MB
PG_MAINT_WORK_MEM=$MW
PG_MAX_WAL_SIZE=$WAL
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
