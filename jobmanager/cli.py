#!/usr/bin/env python3
import os
import json
import time
import subprocess
import argparse
import shlex
from jobmanager import __version__

_TERMINAL_STATES = frozenset({"COMPLETED", "FAILED", "CANCELLED", "TIMEOUT", "OUT_OF_MEMORY"})
_LOG_DIR = "job_logs"


def _fetch_metadata(jobid: str, output_file: str, retries: int = 6, retry_wait: int = 5) -> None:
    """Fetch sacct JSON metadata for *jobid* into *output_file*, retrying until the record appears."""
    for attempt in range(1, retries + 1):
        subprocess.run(
            f"sacct -j {jobid} --json > {output_file}",
            shell=True, check=True
        )
        with open(output_file) as fh:
            data = json.load(fh)
        if data.get("jobs"):
            return
        if attempt < retries:
            print(f"sacct: job {jobid} not yet available, retrying in {retry_wait}s "
                  f"({attempt}/{retries})...", flush=True)
            time.sleep(retry_wait)
    raise RuntimeError(
        f"sacct returned no records for job {jobid} after {retries} attempts. "
        "The job may not be registered in the accounting database yet."
    )


class JobMetadata:
    """Parses `sacct -j <job-id> --json` output and exposes job attributes."""
    def __init__(self, metadata_file: str):
        self.metadata_file = metadata_file
        with open(metadata_file) as fh:
            self.metadata = json.load(fh)
        job = self.metadata["jobs"][0]
        self.user = job["user"]
        self.group = job["group"]
        self.cluster = job["cluster"]
        self.jobid = job["job_id"]
        self.jobname = job["name"]
        self.node_group = job["nodes"]
        self.requested_cpus = job["required"]["CPUs"]
        self.requested_memory = job["required"]["memory"]
        self.partition = job["partition"]
        self.state = job["state"]["current"]
        self.working_dir = job["working_directory"]
        # steps may be empty for pending/just-submitted jobs
        steps = job.get("steps", [])
        self.requested_nodes = steps[0]["nodes"]["count"] if steps else None
        self.elapsed = steps[0]["time"]["elapsed"] if steps else 0


class Job(JobMetadata):
    """Manages a SLURM job. Accepts a metadata file path OR a job ID (not both)."""
    def __init__(self, metadata_file: str = "metadata.json", jobid: str = None):
        metadata_exists = os.path.exists(metadata_file)
        if metadata_exists and jobid is not None:
            raise ValueError("You cannot specify both metadata_file and jobid. Please choose one.")
        elif not metadata_exists and jobid is None:
            raise ValueError("Metadata file does not exist and jobid is not specified. Please specify one of them.")
        elif jobid is not None:
            metadata_file = f"metadata_{jobid}.json"
            self._generate_metadata_file(metadata_file, jobid)
        super().__init__(metadata_file)

    def stop_job(self):
        """Cancels the job via scancel."""
        subprocess.run(["scancel", str(self.jobid)], check=True)

    def update(self, retries: int = 6, retry_wait: int = 5):
        """Refresh all attributes from SLURM. Retries up to *retries* times if sacct
        has not registered the job yet, waiting *retry_wait* seconds between attempts."""
        _fetch_metadata(str(self.jobid), self.metadata_file, retries, retry_wait)
        JobMetadata.__init__(self, self.metadata_file)

    def print_summary(self):
        """Prints a summary of the job information."""
        self.update()
        print(f"Job ID:            {self.jobid}")
        print(f"Job name:          {self.jobname}")
        print(f"User:              {self.user}")
        print(f"Group:             {self.group}")
        print(f"Cluster:           {self.cluster}")
        print(f"Node group:        {self.node_group}")
        print(f"CPUs:              {self.requested_cpus}")
        print(f"Memory:            {self.requested_memory}")
        print(f"Partition:         {self.partition}")
        print(f"Elapsed time:      {self.elapsed}")
        print(f"State:             {self.state}")
        print(f"Working directory: {self.working_dir}")

    def get_status(self):
        """Returns the current status of the job."""
        self.update()
        return self.state

    def get_elapsed_time(self):
        """Returns the elapsed time of the job."""
        self.update()
        return self.elapsed

    def _get_output_file(self) -> str:
        """Returns the resolved stdout path for this job using scontrol."""
        try:
            result = subprocess.run(
                ["scontrol", "show", "job", str(self.jobid)],
                capture_output=True, text=True, check=True,
            )
            for token in result.stdout.split():
                if token.startswith("StdOut="):
                    return token.split("=", 1)[1]
        except subprocess.CalledProcessError:
            pass
        return None

    def watch(self, interval: int = 1):
        """Follows the job's output file in real time until it reaches a terminal state."""
        terminal_states = _TERMINAL_STATES

        output_file = self._get_output_file()
        if not output_file:
            raise RuntimeError(
                f"Could not determine output file for job {self.jobid}. "
                "The job may no longer be in the queue (scontrol only tracks active jobs)."
            )

        print(f"Job {self.jobid} ({self.jobname}) | output: {output_file}")
        print("Press Ctrl+C to stop.\n", flush=True)

        # Wait for job to start and create the output file (may be PENDING)
        while not os.path.exists(output_file):
            state = self.get_status()
            if state in terminal_states:
                print(f"Job ended ({state}) before producing output.")
                return
            print(f"  [{time.strftime('%H:%M:%S')}] {state} — waiting for job to start...", flush=True)
            time.sleep(interval)

        try:
            with open(output_file) as fh:
                while True:
                    chunk = fh.read()
                    if chunk:
                        print(chunk, end="", flush=True)
                    time.sleep(interval)
                    state = self.get_status()
                    if state in terminal_states:
                        # Final drain
                        chunk = fh.read()
                        if chunk:
                            print(chunk, end="", flush=True)
                        print(f"\n[{time.strftime('%H:%M:%S')}] Job {self.jobid} — {state}")
                        break
        except KeyboardInterrupt:
            print("\nWatch interrupted by user.")

    def _generate_metadata_file(self, metadata_file: str, jobid: str):
        """Fetches metadata for *jobid* from SLURM and writes it to *metadata_file*."""
        if os.path.exists(metadata_file):
            raise FileExistsError(
                f"File {metadata_file} already exists. "
                "Please choose a different name or delete the existing file."
            )
        subprocess.run(
            f"sacct -j {jobid} --json > {metadata_file}",
            shell=True, check=True
        )
        return metadata_file

    def print_request_script(self):
        """Prints a SLURM batch script with the same resource requests as this job."""
        print("#!/bin/bash")
        print(f"#SBATCH --job-name={self.jobname}")
        print(f"#SBATCH --partition={self.partition}")
        print(f"#SBATCH --nodes={self.requested_nodes}")
        print(f"#SBATCH --cpus-per-task={self.requested_cpus}")
        print(f"#SBATCH --mem={self.requested_memory}")

    def _get_submitline(self) -> str:
        """Fetches submitline from sacct and returns the second output line as a shell command."""
        try:
            result = subprocess.run(
                ["sacct", "-j", str(self.jobid), "-o", "submitline", "-P"],
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise RuntimeError(
                f"Failed to retrieve submitline for job {self.jobid} using sacct. {stderr}"
            ) from exc

        lines = result.stdout.splitlines()
        if len(lines) < 2:
            raise RuntimeError(
                f"sacct did not return a submitline for job {self.jobid}. Output: {result.stdout!r}"
            )

        submitline = lines[1].rstrip("|").strip()
        if not submitline:
            raise RuntimeError(f"Empty submitline returned for job {self.jobid}.")
        return submitline

    def print_request_cmd(self) -> str:
        """Prints and returns the original submission command from sacct submitline."""
        submitline = self._get_submitline()
        print(submitline)
        return submitline

    def submit(self) -> str:
        """Resubmits the job by executing the command from sacct submitline."""
        submitline = self._get_submitline()
        argv = shlex.split(submitline)
        if not argv:
            raise RuntimeError(f"Empty submitline returned for job {self.jobid}.")

        # sacct may return an unquoted --wrap command. If present, rebuild it
        # as a single argument using all trailing tokens.
        if "--wrap" in argv:
            wrap_idx = argv.index("--wrap")
            if wrap_idx + 1 >= len(argv):
                raise RuntimeError(f"Invalid submitline for job {self.jobid}: missing value for --wrap.")
            wrap_cmd = " ".join(argv[wrap_idx + 1:])
            argv = argv[:wrap_idx + 2]
            argv[wrap_idx + 1] = wrap_cmd
        else:
            wrap_eq_idx = next((i for i, token in enumerate(argv) if token.startswith("--wrap=")), -1)
            if wrap_eq_idx != -1:
                wrap_first = argv[wrap_eq_idx].split("=", 1)[1]
                wrap_cmd = " ".join([wrap_first] + argv[wrap_eq_idx + 1:]).strip()
                if not wrap_cmd:
                    raise RuntimeError(f"Invalid submitline for job {self.jobid}: empty value for --wrap.")
                argv = argv[:wrap_eq_idx] + ["--wrap", wrap_cmd]

        try:
            result = subprocess.run(
                argv,
                capture_output=True,
                text=True,
                check=True,
            )
        except subprocess.CalledProcessError as exc:
            stderr = (exc.stderr or "").strip()
            raise RuntimeError(
                f"Failed to submit job using submitline for job {self.jobid}. {stderr}"
            ) from exc

        output = result.stdout.strip()
        parts = output.split()
        if parts and parts[-1].isdigit():
            new_job_id = parts[-1]
            # Point this instance to the new job and refresh metadata.json via update()
            self.jobid = new_job_id
            self.update()
            return new_job_id
        return output


# ---------------------------------------------------------------------------
# Module-level helpers (do not require an existing Job instance)
# ---------------------------------------------------------------------------

def list_jobs(user: str = None) -> None:
    """Prints the active jobs for *user* (defaults to current user)."""
    if user is None:
        user = os.environ.get("USER", os.environ.get("LOGNAME", ""))
    fmt = "%.10i %.20j %.8u %.8T %.10M %.6D %R"
    subprocess.run(["squeue", "-u", user, f"--format={fmt}"], check=True)


def _check_and_handle_active_job(metadata_file: str = "metadata.json") -> bool:
    """Check whether the job tracked in *metadata_file* is still active.

    If active, warn the user and offer to cancel it.
    Returns True if it is safe to proceed, False if the user chose to abort.
    """
    if not os.path.exists(metadata_file):
        return True
    try:
        job = Job(metadata_file=metadata_file)
        job.update()
    except Exception:
        return True  # Cannot determine state; allow proceeding
    if job.state not in _TERMINAL_STATES:
        print(
            f"Warning: job {job.jobid} ({job.jobname}) is currently {job.state}.\n"
            "You must cancel it before proceeding."
        )
        confirm = input("Cancel the active job and continue? [y/N] ").strip().lower()
        if confirm == "y":
            job.stop_job()
            print(f"Job {job.jobid} cancelled.")
            return True
        print("Aborted.")
        return False
    return True


def _validate_script_resources(script: str) -> list:
    """Return a list of #SBATCH resource directives set to zero in *script*.

    Checks --nodes, --ntasks, --cpus-per-task, --mem, and --time.
    A time value is considered zero when all its digit characters are '0'.
    """
    resource_keys = {"--nodes", "--ntasks", "--cpus-per-task", "--mem", "--time"}
    offenders = []
    with open(script) as fh:
        for line in fh:
            line = line.strip()
            if not line.startswith("#SBATCH"):
                continue
            parts = line.split(None, 1)
            if len(parts) < 2 or "=" not in parts[1]:
                continue
            key, val = parts[1].split("=", 1)
            key, val = key.strip(), val.strip()
            if key not in resource_keys:
                continue
            if key == "--time":
                is_zero = val and all(c == "0" for c in val if c.isdigit())
            elif key == "--mem":
                numeric = val.rstrip("GgMmKkBb")
                try:
                    is_zero = float(numeric) == 0
                except ValueError:
                    is_zero = False
            else:
                try:
                    is_zero = int(val) == 0
                except ValueError:
                    is_zero = False
            if is_zero:
                offenders.append(f"{key}={val}")
    return offenders


def _create_template(quick: bool = False, medium: bool = False, test: bool = False) -> None:
    """Write a SLURM batch script template (run.sh) in the current directory.

    Preset sizes — test: minimal (1 node/task/CPU, 1 GB, 1 min);
    quick: small (1 node, 1 task, 2 CPUs, 4 GB, 1 h);
    medium: moderate (1 node, 4 tasks, 8 CPUs, 32 GB, 24 h);
    default: all zeros (must be filled manually before submitting).
    """
    dest = "run.sh"
    if os.path.exists(dest):
        raise SystemExit(f"{dest} already exists in the current directory.")
    dirname = os.path.basename(os.getcwd())
    if test:
        nodes, ntasks, cpus, mem, walltime = 1, 1, 1, "1G", "0-00:01:00"
    elif quick:
        nodes, ntasks, cpus, mem, walltime = 1, 1, 2, "4G", "0-01:00:00"
    elif medium:
        nodes, ntasks, cpus, mem, walltime = 1, 4, 8, "32G", "1-00:00:00"
    else:
        nodes, ntasks, cpus, mem, walltime = 0, 0, 0, "0G", "0-00:00:00"
    content = (
        "#!/bin/bash\n"
        f"#SBATCH --job-name={dirname}\n"
        f"#SBATCH --nodes={nodes}\n"
        f"#SBATCH --ntasks={ntasks}\n"
        f"#SBATCH --cpus-per-task={cpus}\n"
        f"#SBATCH --mem={mem}\n"
        f"#SBATCH --time={walltime}\n"
        f"#SBATCH --output={_LOG_DIR}/LOG_%x_%j.log\n"
        "\n"
        "sleep 10\n"
        "sacct -j $SLURM_JOBID --json > metadata.json\n"
        "\n"
        "# === INSERT YOUR CODE BELOW ===\n"
    )
    with open(dest, "w") as fh:
        fh.write(content)
    os.chmod(dest, 0o755)
    print(f"Created {dest}  (job-name: {dirname})")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="jobmanager",
        description="Manage SLURM jobs via sacct/squeue/sbatch/scancel.",
    )
    parser.add_argument(
        "--version", action="version", version=f"%(prog)s {__version__}"
    )
    subparsers = parser.add_subparsers(dest="command", metavar="COMMAND")
    subparsers.required = True

    # --- helpers shared by job-specific subcommands ---
    def add_job_args(p):
        group = p.add_mutually_exclusive_group(required=False)
        group.add_argument("--jobid", metavar="ID", help="SLURM job ID")
        group.add_argument(
            "--metadata", metavar="FILE",
            help="Path to sacct JSON metadata file (default: metadata.json in cwd if it exists)."
        )

    # status
    p_status = subparsers.add_parser("status", help="Print the current state of a job.")
    add_job_args(p_status)

    # summary
    p_summary = subparsers.add_parser("summary", help="Print a full summary of a job.")
    add_job_args(p_summary)

    # elapsed
    p_elapsed = subparsers.add_parser("elapsed", help="Print the elapsed time of a job.")
    add_job_args(p_elapsed)

    # stop
    p_stop = subparsers.add_parser("stop", help="Cancel a running job (scancel).")
    add_job_args(p_stop)
    p_stop.add_argument(
        "--yes", action="store_true",
        help="Skip confirmation prompt."
    )

    # print-request
    p_write = subparsers.add_parser(
        "print-request",
        help="Print a SLURM batch script with the same resource requests (redirect to save)."
    )
    add_job_args(p_write)

    # watch
    p_watch = subparsers.add_parser(
        "watch",
        help="Stream the job's output file in real time until it reaches a terminal state."
    )
    add_job_args(p_watch)
    p_watch.add_argument(
        "--interval", type=int, default=1, metavar="N",
        help="Seconds between reads/state checks (default: 1)."
    )

    # resubmit
    p_submit = subparsers.add_parser(
        "resubmit",
        help="Resubmit using the command from: sacct -j <jobid> -o submitline -P."
    )
    add_job_args(p_submit)

    # template
    p_template = subparsers.add_parser(
        "template",
        help="Create a run.sh SLURM batch script template in the current directory.",
    )
    size = p_template.add_mutually_exclusive_group()
    size.add_argument(
        "--test", action="store_true",
        help="Minimal resources: 1 node, 1 task, 1 CPU, 1 GB, 1 min.",
    )
    size.add_argument(
        "--quick", action="store_true",
        help="Small resources: 1 node, 1 task, 2 CPUs, 4 GB, 1 h.",
    )
    size.add_argument(
        "--medium", action="store_true",
        help="Medium resources: 1 node, 4 tasks, 8 CPUs, 32 GB, 24 h.",
    )

    # submit
    p_submit_cmd = subparsers.add_parser(
        "submit",
        help="Submit a batch script via sbatch (default: run.sh in the current directory).",
    )
    p_submit_cmd.add_argument(
        "script", nargs="?", default=None, metavar="SCRIPT",
        help="Path to the batch script to submit (default: run.sh).",
    )

    # list
    p_list = subparsers.add_parser("list", help="List active jobs (squeue).")
    p_list.add_argument(
        "--user", metavar="USER", default=None,
        help="Username to filter by (default: current user)."
    )

    return parser


def _load_job(args) -> "Job":
    """Instantiate Job from --jobid, --metadata, or the default metadata.json in cwd."""
    if args.jobid is not None:
        return Job(jobid=args.jobid)
    if args.metadata is not None:
        return Job(metadata_file=args.metadata)
    default = "metadata.json"
    if os.path.exists(default):
        return Job(metadata_file=default)
    raise SystemExit(
        "No job specified. Use --jobid or --metadata, "
        "or place a metadata.json file in the current directory."
    )


def main():
    parser = _build_parser()
    args = parser.parse_args()

    if args.command == "status":
        job = _load_job(args)
        print(job.get_status())

    elif args.command == "summary":
        job = _load_job(args)
        job.print_summary()

    elif args.command == "elapsed":
        job = _load_job(args)
        print(job.get_elapsed_time())

    elif args.command == "stop":
        job = _load_job(args)
        if not args.yes:
            confirm = input(f"Cancel job {job.jobid} ({job.jobname})? [y/N] ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return
        job.stop_job()
        print(f"Job {job.jobid} cancelled.")

    elif args.command == "print-request":
        job = _load_job(args)
        job.print_request_script()

    elif args.command == "watch":
        job = _load_job(args)
        job.watch(interval=args.interval)

    elif args.command == "template":
        _create_template(quick=args.quick, medium=args.medium, test=args.test)

    elif args.command == "submit":
        script = args.script if args.script is not None else "run.sh"
        if not os.path.exists(script):
            raise SystemExit(f"Script not found: {script!r}")
        zero_params = _validate_script_resources(script)
        if zero_params:
            raise SystemExit(
                "The following resource parameters are still set to zero in the script:\n"
                + "".join(f"  {p}\n" for p in zero_params)
                + "Edit the script and set valid resource values before submitting."
            )
        if not _check_and_handle_active_job():
            return
        os.makedirs(_LOG_DIR, exist_ok=True)
        try:
            result = subprocess.run(
                ["sbatch", script], capture_output=True, text=True, check=True
            )
        except subprocess.CalledProcessError as exc:
            raise SystemExit((exc.stderr or exc.stdout).strip()) from exc
        output = result.stdout.strip()
        parts = output.split()
        if not (parts and parts[-1].isdigit()):
            raise SystemExit(f"Could not parse job ID from sbatch output: {output!r}")
        new_job_id = parts[-1]
        _fetch_metadata(new_job_id, "metadata.json")
        print(f"Submitted - job ID: {new_job_id}")

    elif args.command == "resubmit":
        if not _check_and_handle_active_job():
            return
        job = _load_job(args)
        job_id = job.submit()
        print(f"Submitted - job ID: {job_id}")

    elif args.command == "list":
        list_jobs(user=args.user)


if __name__ == "__main__":
    main()