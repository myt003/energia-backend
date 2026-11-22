#!/usr/bin/env python3
import os
import re
import subprocess
import time
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
CLOUDFLARED = Path(os.environ.get("CLOUDFLARED", ROOT.parent / "tools/cloudflared"))
LOG_PATH = Path("/tmp/opencode/energia-cloudflared.log")
PID_PATH = Path("/tmp/opencode/energia-cloudflared.pid")
URL_PATH = Path("/tmp/opencode/energia-cloudflared.url")


def main():
    if not CLOUDFLARED.exists():
        raise SystemExit(f"cloudflared not found: {CLOUDFLARED}")
    if PID_PATH.exists():
        try:
            os.kill(int(PID_PATH.read_text().strip()), 0)
        except (ProcessLookupError, ValueError):
            PID_PATH.unlink(missing_ok=True)
        else:
            if URL_PATH.exists():
                print(URL_PATH.read_text().strip())
                return
            raise SystemExit("Tunnel is running but its URL is unavailable")
    LOG_PATH.write_text("")
    with LOG_PATH.open("ab", buffering=0) as log:
        process = subprocess.Popen(
            [
                str(CLOUDFLARED),
                "tunnel",
                "--url",
                "http://127.0.0.1:8000",
                "--no-autoupdate",
                "--loglevel",
                "info",
            ],
            stdin=subprocess.DEVNULL,
            stdout=log,
            stderr=subprocess.STDOUT,
            start_new_session=True,
        )
    PID_PATH.write_text(str(process.pid))
    pattern = re.compile(r"https://[a-z0-9-]+\.trycloudflare\.com")
    deadline = time.monotonic() + 30
    while time.monotonic() < deadline:
        content = LOG_PATH.read_text(errors="replace")
        match = pattern.search(content)
        if match:
            URL_PATH.write_text(match.group(0))
            print(match.group(0))
            return
        if process.poll() is not None:
            raise SystemExit(f"cloudflared stopped unexpectedly; see {LOG_PATH}")
        time.sleep(0.25)
    process.terminate()
    raise SystemExit(f"Timed out waiting for tunnel URL; see {LOG_PATH}")


if __name__ == "__main__":
    main()
