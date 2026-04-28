# jobmanager

A command-line tool to manage SLURM jobs via `sacct`, `squeue`, `sbatch`, and `scancel`.

## Requirements

- Python 3.7+
- SLURM (`sacct`, `squeue`, `scontrol`, `scancel`)
- PyYAML (`pip install pyyaml`, included as a dependency)

## Installation

```bash
pip install slurm-jobmanager
```

Or install directly from the repository:

```bash
git clone https://github.com/Dannyzimmer/SlurmJobManager
cd SlurmJobManager
pip install .
```

## Intended workflow

>[!Important]
> `jobmanager` is designed to run only **one job at a time for a given directory**. This encourages an organized workflow where each job has its own directory with associated metadata and logs. Running **multiple jobs in the same directory is not supported** and may lead to incorrect behavior.

Each job lives in its own directory containing a `metadata.json` file (auto generated via `sacct -j <jobid> --json > metadata.json`). The typical usage pattern is:

```
jobs/
├── run_001/
│   ├── metadata.json
│   └── job_logs
│       ├── LOG_run_001_6552064.log
│       └── LOG_run_001_6552081.log
├── run_002/
│   ├── metadata.json
│   └── job_logs
│       ├── LOG_run_001_6552092.log
│       └── LOG_run_001_6552101.log
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
If the current job is still active, prompts to cancel it first.
```bash
jobmanager resubmit
```

### `template`
Create a `run.sh` SLURM batch script in the current directory. Job name is set
automatically from the directory name.
```bash
jobmanager template            # blank placeholders (must be filled before submitting)
jobmanager template --test     # 1 node, 1 task, 1 CPU, 1 GB, 1 min
jobmanager template --quick    # 1 node, 1 task, 2 CPUs, 4 GB, 1 h
jobmanager template --medium   # 1 node, 4 tasks, 8 CPUs, 32 GB, 24 h
```

If a `requirements.txt` is present in the current directory, `jobmanager template`
automatically appends a virtualenv setup block to the script:

```bash
# module load python/3.11   ← only if module_loads is set in config
python3 -m venv .venv         # or ${TMPDIR}/.venv_${SLURM_JOBID} if tmp_dir_var is set
source .venv/bin/activate
pip install -r requirements.txt
```

### `config`
Manage the configuration file (`~/.config/jobmanager/config.yaml`).
```bash
jobmanager config init    # create the config file with documented defaults
jobmanager config edit    # open the config file in an interactive editor
```

Available settings:

| Key | Default | Description |
|---|---|---|
| `tmp_dir_var` | *(unset)* | Environment variable holding the node's local temp dir (e.g. `TMPDIR`, `LOCAL_SCRATCH`). Used for venv placement. |
| `module_loads` | `[]` | List of environment modules to load before creating a virtualenv. |
| `log_dir` | `job_logs` | Directory for SLURM log files (relative to the working directory). |
| `sacct_retries` | `6` | Times to retry `sacct` when a job is not yet registered in the accounting DB. |

### `submit`
Validate and submit a batch script via `sbatch`. Checks for zero-value resource
directives and warns if the current directory's job is still active.
```bash
jobmanager submit              # uses run.sh in the current directory
jobmanager submit path/to/script.sh
```
