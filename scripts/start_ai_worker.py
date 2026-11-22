#!/usr/bin/env python3
import os
import subprocess
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
PID_PATH = Path("/tmp/opencode/energia-ai-worker.pid")
LOG_PATH = Path("/tmp/opencode/energia-ai-worker.log")


def main():
    if PID_PATH.exists():
        try:
            os.kill(int(PID_PATH.read_text().strip()), 0)
        except (ProcessLookupError, ValueError):
            PID_PATH.unlink(missing_ok=True)
        else:
            print(f"AI worker already running: {PID_PATH.read_text().strip()}")
            return
    with LOG_PATH.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [
                str(ROOT / ".venv/bin/python"),
                str(ROOT / "manage.py"),
                "process_ai_jobs",
                "--interval",
                "1",
            ],
            cwd=ROOT,
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_PATH.write_text(str(process.pid))
    print(f"AI worker started: {process.pid}")


if __name__ == "__main__":
    main()
