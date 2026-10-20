# Benchmark VM runbook

Goal: run the official tests on a clean Linux VM with dedicated CPUs, then delete the VM.
Budget: about $10. Expected run time 4 to 6 hours.

## 1. Rent the VM

- Provider: Hetzner Cloud, server type **CCX23** (4 dedicated vCPU, 16 GB RAM, 160 GB NVMe), location Nuremberg or Helsinki, image **Ubuntu 24.04**.
- Price: about $0.155 per hour at last check (third-party tracker; Hetzner raised cloud prices on 15 June 2026, so confirm on the order page). Six hours is about $1.
- Any provider works if it offers 4 dedicated vCPU and 16 GB RAM. Shared-CPU plans are not acceptable: noisy neighbours would distort the latencies.
- Add your SSH public key when creating the server. On Windows, in PowerShell: `ssh-keygen -t ed25519` then `Get-Content $env:USERPROFILE\.ssh\id_ed25519.pub` and paste that into the provider.

## 2. Set it up

```
ssh root@<server-ip>
git clone https://github.com/capt-jack-21/tsdb-multitenant-bench.git
cd tsdb-multitenant-bench
bash bench/vm/setup.sh
```

This installs Docker and pgbench, starts TimescaleDB with the settings in `.env`, and writes `results/official/vm_environment.txt` (image digest, CPU, RAM, versions, commit). Copy that into section 10 of `PROTOCOL.md` afterwards.

## 3. Run

```
tmux new -s bench
bash bench/vm/run_all.sh
```

Detach with Ctrl-b then d. The run continues if SSH drops. Reattach with `tmux attach -t bench`. Progress: `tail -f logs/run_all.log`.

The script stops immediately if a smoke test fails. Send the error back before spending more VM time.

## 4. Get the results out, then delete the server

From your PC, in PowerShell:

```
cd C:\Users\Adithya\Desktop\tsdb-multitenant-bench
scp -r root@<server-ip>:~/tsdb-multitenant-bench/results/official .\results\
scp -r root@<server-ip>:~/tsdb-multitenant-bench/results/official_prepared .\results\
scp -r root@<server-ip>:~/tsdb-multitenant-bench/logs .\logs
```

**Then delete the server in the provider console.** A stopped server is still billed. Check that the console shows no servers left.

## 5. If something fails halfway

Re-run only the failed step, for example `.venv/bin/python bench/run_queries.py --results-dir results/official`. Results append to the same files, so delete the partial files for that test first. Never mix runs from different VMs in one summary.
