#!/usr/bin/env python3
"""Compile the explicitly supported schema dialect into a reviewable PG artifact."""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
sources = [ROOT / 'avenra/schema.sql', *sorted((ROOT / 'avenra/migrations').glob('*.sql'))]
parts = ['-- Avenra control schema, version 9. Generated from the owned SQL migrations.\n', "CREATE FUNCTION avenra_immutable_record() RETURNS trigger LANGUAGE plpgsql AS $$ BEGIN RAISE EXCEPTION 'Published records are immutable'; END; $$;\n"]
for source in sources:
    text = source.read_text()
    def trigger(match):
        name, operation, table, condition = match.group(1, 2, 3, 4)
        when = ' WHEN (OLD.evidence_json IS NOT NULL)' if condition else ''
        return f'CREATE TRIGGER {name} BEFORE {operation} ON {table} FOR EACH ROW{when} EXECUTE FUNCTION avenra_immutable_record();'
    text = re.sub(r"CREATE TRIGGER ([a-z_]+) BEFORE (UPDATE(?: OF [a-z_]+)?|DELETE) ON ([a-z_]+) (WHEN OLD.evidence_json IS NOT NULL )?BEGIN SELECT RAISE\(ABORT,'[^']+'\); END;", trigger, text)
    text = text.replace("typeof(amount_paise)='integer' AND ", '')
    text = re.sub(r'\bINTEGER PRIMARY KEY\b', 'BIGSERIAL PRIMARY KEY', text)
    text = re.sub(r'\bINTEGER\b', 'BIGINT', text)
    text = re.sub(r'\bREAL\b', 'DOUBLE PRECISION', text)
    assert 'RAISE(ABORT' not in text and 'typeof(' not in text, source
    parts.append('-- ' + source.name + '\n' + text + '\n')
parts.append('CREATE TABLE avenra_schema_version(version BIGINT PRIMARY KEY);\nINSERT INTO avenra_schema_version VALUES(9);\n')
target = ROOT / 'avenra/postgres_schema.sql'
target.write_text('\n'.join(parts))
print('Generated', target)
