"""UTC database sessions for the native journal's zone-less timestamp columns."""
from pathlib import Path


def enforce(root):
    import psycopg
    from psycopg import sql
    from psycopg.conninfo import conninfo_to_dict
    from .local_database import admin_dsn
    from .lab_identity import private_read
    root=Path(root);path=root/'.local/data/accounting.dsn'
    if not path.exists():return {'configured':False,'reason':'Private accounting connection is not configured.'}
    connection=conninfo_to_dict(private_read(path).decode().strip());role,database=connection['user'],connection['dbname']
    with psycopg.connect(admin_dsn(root),autocommit=True) as db:
        db.execute(sql.SQL("ALTER DATABASE {} SET timezone TO 'UTC'").format(sql.Identifier(database)))
        db.execute(sql.SQL("ALTER ROLE {} IN DATABASE {} SET timezone TO 'UTC'").format(sql.Identifier(role),sql.Identifier(database)))
    return {'configured':True,'database':database,'role':role,'session_timezone':'UTC','scope':'Only the owned accounting role/database. Existing journal records are not rewritten.'}
