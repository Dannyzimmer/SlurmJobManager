# jobmanager

A command-line tool to manage SLURM jobs via `sacct`, `squeue`, `sbatch`, and `scancel`.

## Requirements

- Python 3
- SLURM (`sacct`, `squeue`, `scontrol`, `scancel`)

## Installation

```bash
chmod +x jobmanager
# Optionally, add to PATH
cp jobmanager ~/.local/bin/
```

## Intended workflow

Each job lives in its own directory containing a `metadata.json` file (generated via `sacct -j <jobid> --json > metadata.json`). The typical usage pattern is:

```
jobs/
├── run_001/
│   ├── metadata.json
│   └── slurm-12345.out
├── run_002/
│   ├── metadata.json
│   └── slurm-12346.out
```

Navigate to the job directory and run commands without extra flags — `jobmanager` picks up `metadata.json` automatically:

```bash
cd jobs/run_001
jobmanager status
jobmanager watch
```

## Job identification

All job-specific commands resolve the target job in this order:

1. `--jobid ID` — fetch metadata from SLURM on the fly
2. `--metadata FILE` — use an explicit metadata file
3. *(default)* — use `metadata.json` in the current directory

## Commands

### `list`
List your active jobs (wraps `squeue`).
```bash
jobmanager list
jobmanager list --user alice
```

### `status`
Print the current state of a job (`RUNNING`, `PENDING`, `COMPLETED`, …).
```bash
jobmanager status
```

### `summary`
Print a full summary: partition, CPUs, memory, elapsed time, state, working directory, etc.
```bash
jobmanager summary
```

### `elapsed`
Print the elapsed wall-clock time of a job.
```bash
jobmanager elapsed
```

### `watch`
Stream the job's stdout in real time until it reaches a terminal state.
```bash
jobmanager watch
jobmanager watch --interval 10   # poll every 10 s (default: 5)
```

### `stop`
Cancel a job (`scancel`). Prompts for confirmation unless `--yes` is passed.
```bash
jobmanager stop
jobmanager stop --yes
```

### `print-request`
Print a SLURM batch script header reproducing the resource requests of the job.
Redirect to a file to reuse it.
```bash
jobmanager print-request > job.sh
```

### `resubmit`
Resubmit a job using the original submission command recovered from `sacct`.
```bash
jobmanager resubmit
```
