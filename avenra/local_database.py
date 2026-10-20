"""Private local database connections; credentials never belong in command arguments."""
from pathlib import Path


def admin_dsn(root):
    path = Path(root) / '.local/data/database-admin.dsn'
    if path.exists():
        if path.stat().st_mode & 0o077:
            raise RuntimeError('Database connection file requires private permissions.')
        return path.read_text().strip()
    return 'postgresql://avenra@127.0.0.1:55437/postgres'
