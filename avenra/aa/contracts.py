"""Byte-pinned public contracts; upstream inconsistencies remain explicit."""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import shutil
import uuid

import yaml
from jsonschema import Draft4Validator, FormatChecker
from lxml import etree

from ..errors import DomainError, require

ARTIFACTS = {
    'aa': ('aa.yaml', 'AA_2_1_0.yaml', 'e6fd56713b5775196491389064deb51e8cafd62a681c881e6eeb6aab37ae0395', '2.1.0'),
    'fip': ('fip.yaml', 'FIP_2_2_0.yaml', 'f4b0a0fa6167624408989b833e571f8f11e829dc5b3acaf1a2e7ebfa530cd023', '2.2.0'),
    'fiu': ('fiu.yaml', 'FIU_2_0_0.yaml', '77c96cd8336c6de4a7489952365764ad5a2676f1d5823ccfdfb21d87c518238b', '2.0.0'),
    'deposit': ('deposit.xsd', 'deposit_v2.0.0.xsd', 'e9307c5dafa81a241e6f1a3293ce2bebc36dfbd86e97d263a303082e2da9345d', '2.0.0'),
}
VERSIONS = {role: value[3] for role, value in ARTIFACTS.items()}
FORMAT = FormatChecker()


def timestamp(value):
    require(isinstance(value, str) and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d(?:\.\d{1,9})?(?:Z|[+-]\d\d:\d\d)', value), 'InvalidRequest', 'Use an RFC 3339 timestamp.')
    try: result = datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()
    except (ValueError, OverflowError): raise DomainError('InvalidRequest', 'Invalid timestamp.')
    return result


def stamp(value=None):
    return datetime.fromtimestamp(value, timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z') if value is not None else datetime.now(timezone.utc).isoformat(timespec='milliseconds').replace('+00:00', 'Z')


@FORMAT.checks('date-time', raises=(DomainError,))
def check_datetime(value):
    if not isinstance(value, str): return True
    timestamp(value);return True


def identifier(value):
    try: return str(uuid.UUID(value)) == value
    except (ValueError, TypeError, AttributeError): return False


def install(directory, cache):
    destination = Path(directory) / 'aa-contracts'
    destination.mkdir(parents=True, mode=0o700, exist_ok=True)
    records = []
    for role, (filename, official, expected, version) in ARTIFACTS.items():
        raw = (Path(cache) / filename).read_bytes()
        require(hashlib.sha256(raw).hexdigest() == expected, 'CONTRACT_INTEGRITY', 'Cached public contract differs from the selected immutable profile.', 409)
        target = destination / filename
        if target.exists(): require(target.read_bytes() == raw, 'CONTRACT_CONFLICT', 'Preserve the installed contract version.', 409)
        else: target.write_bytes(raw);target.chmod(0o600)
        records.append({'role': role, 'file': filename, 'official_file': official, 'sha256': expected, 'selected_version': version})
    return records


def strict_schema(value):
    if isinstance(value, list): return [strict_schema(x) for x in value]
    if not isinstance(value, dict): return value
    result = {k: ({name: strict_schema(schema) for name, schema in v.items()} if k in {'properties', 'definitions'} else strict_schema(v)) for k, v in value.items() if k not in {'description', 'example', 'xml'}}
    if 'properties' in result:
        result.setdefault('type', 'object');result.setdefault('additionalProperties', False)
    return result


class Contracts:
    def __init__(self, directory):
        self.directory = Path(directory) / 'aa-contracts';self.specs = {};self.validators = {}
        for role, (filename, _, expected, _) in ARTIFACTS.items():
            path = self.directory / filename
            require(path.is_file() and not path.is_symlink(), 'AA_CONTRACTS_UNAVAILABLE', 'Install the selected public financial-data contracts before enabling this profile.', 503)
            raw = path.read_bytes()
            require(hashlib.sha256(raw).hexdigest() == expected, 'CONTRACT_INTEGRITY', 'Installed financial-data contract failed its byte binding.', 503)
            if role == 'deposit':
                self.xsd = etree.XMLSchema(etree.fromstring(raw, parser=etree.XMLParser(resolve_entities=False, no_network=True)))
            else: self.specs[role] = yaml.safe_load(raw)

    def metadata(self):
        return [{'role': role, 'selected_version': VERSIONS[role], 'declared_version': str(spec['info']['version']),
                 'source_hash': ARTIFACTS[role][2], 'metadata_matches': VERSIONS[role] == str(spec['info']['version'])} for role, spec in self.specs.items()]

    def route(self, role, method, path):
        for template, operations in self.specs[role]['paths'].items():
            pattern = re.sub(r'\{[A-Za-z]+\}', r'([A-Za-z0-9_-]+)', template)
            matched = re.fullmatch(pattern, path)
            if matched and method.lower() in operations: return template, operations[method.lower()], matched.groups()
        raise DomainError('NotFound', 'Financial-data route not found.', 404)

    def model(self, role, schema, value):
        key = role, json.dumps(schema, sort_keys=True)
        validator = self.validators.get(key)
        if validator is None:
            validator = Draft4Validator({**strict_schema(schema), 'definitions': strict_schema(self.specs[role]['definitions'])}, format_checker=FORMAT)
            self.validators[key] = validator
        errors = sorted(validator.iter_errors(value), key=lambda x: str(x.path))
        require(not errors, 'InvalidRequest', 'Payload does not match the selected public contract' + (': ' + '/'.join(map(str, errors[0].absolute_path)) if errors else '') + '.')
        return value

    def validate(self, role, method, path, value, response=False):
        _, operation, _ = self.route(role, method, path)
        schema = operation['responses']['200'].get('schema') if response else next((x['schema'] for x in operation.get('parameters', []) if x.get('in') == 'body'), None)
        if schema: self.model(role, schema, value)
        return value

    def document(self, xml):
        require(isinstance(xml, bytes) and len(xml) <= 2_000_000 and b'<!DOCTYPE' not in xml.upper() and b'<!ENTITY' not in xml.upper(), 'InvalidFI', 'Use bounded financial XML without external declarations.')
        try:
            root = etree.fromstring(xml, parser=etree.XMLParser(resolve_entities=False, no_network=True, huge_tree=False))
            self.xsd.assertValid(root)
        except (etree.XMLSyntaxError, etree.DocumentInvalid): raise DomainError('InvalidFI', 'Financial XML does not match the pinned deposit schema.')
        return root
