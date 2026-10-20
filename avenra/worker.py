"""Run durable workers independently from API request serving."""
import argparse
import signal
import threading
from pathlib import Path

from .app import App


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--data', required=True)
    args = parser.parse_args()
    data = Path(args.data).resolve()
    dsn_file = data / 'control.dsn'
    if not dsn_file.exists() or dsn_file.stat().st_mode & 0o077:
        raise SystemExit('Separate workers require a private PostgreSQL connection file.')
    app = App(data, control_dsn=dsn_file.read_text().strip())
    release = app.runtime.configuration().get('release', {})
    if release and release['application_fingerprint'] != app.source_fingerprint:
        app.close();raise SystemExit('Installed worker differs from the pinned deployment release.')
    stopped = threading.Event()
    signal.signal(signal.SIGTERM, lambda *_: stopped.set())
    signal.signal(signal.SIGINT, lambda *_: stopped.set())
    app.start_worker()
    try:
        stopped.wait()
    finally:
        app.close()


if __name__ == '__main__':
    main()
