"""Run the backend directly, restarting its whole process group on Python edits."""
import os
from pathlib import Path
import signal
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]


def snapshot():
    return {str(p): p.stat().st_mtime_ns for p in (ROOT / 'app').rglob('*.py')}


def stop(child):
    # Kill yt-dlp subprocesses too, before starting another worker on the same DB.
    try:
        os.killpg(child.pid, signal.SIGTERM)
    except ProcessLookupError:
        return
    try:
        child.wait(timeout=5)
    except subprocess.TimeoutExpired:
        pass
    try:
        os.killpg(child.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass
    child.wait()


def main():
    os.chdir(ROOT)
    interpreter = ROOT / '.venv/bin/python'
    if not interpreter.exists():
        raise SystemExit('Run make local-setup first to create .venv and install dependencies.')
    child = None
    signal.signal(signal.SIGTERM, lambda *_: sys.exit(0))
    try:
        while True:
            before = snapshot()
            child = subprocess.Popen([str(interpreter), '-u', '-m', 'app.server'], start_new_session=True)
            while child.poll() is None:
                time.sleep(0.5)
                if snapshot() != before:
                    print('Python changed: restarting app and workers.', flush=True)
                    stop(child)
                    break
            else:
                return child.returncode
    finally:
        if child is not None:
            stop(child)


if __name__ == '__main__':
    sys.exit(main())
