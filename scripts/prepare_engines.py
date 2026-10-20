#!/usr/bin/env python3
"""Materialise pinned, licensed source with recorded, repeatable adaptations."""
import argparse
import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import tarfile
import urllib.request

ROOT = Path(__file__).resolve().parents[1]
CACHE = Path.home() / ".local/share/avenra/source-cache"
SKIP = {".git", ".github", ".agents", ".claude", "node_modules", "target", "__pycache__"}
NOTICE_NAMES = {"LICENSE", "LICENSE.md", "LICENSE.txt", "NOTICE", "NOTICE.txt", "COPYING", "COPYRIGHT"}
MARK = "Modified for Avenra. Original attribution and adaptation record: LICENSES/."


def adapt(data, path, component):
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return data, False
    original = text
    # Existing copyright/SPDX notices are retained verbatim, even in source.
    protected = {}
    def protect(match):
        key = f"__AVENRA_NOTICE_{len(protected)}__"
        protected[key] = match.group(0)
        return key
    text = re.sub(r'/\*[\s\S]*?\*/', lambda m: protect(m) if re.search(r'copyright|SPDX-License-Identifier', m.group(), re.I) else m.group(), text)
    text = re.sub(r"(?im)^.*(?:copyright|SPDX-License-Identifier).*$", protect, text)
    for before, after in component['replacements']:
        if before in component.get('boundary_tokens', []):
            text = re.sub(r"(?<![A-Za-z0-9])" + re.escape(before), lambda _: after, text)
        else:
            text = text.replace(before, after)
    for before, after in component.get('extra_replacements', []):
        text = text.replace(before, after)
    for key, value in protected.items():
        text = text.replace(key, value)
    if path.as_posix() == '.gitignore' and component.get('git_exceptions'):
        text = text.rstrip() + '\n\n# Modified for Avenra: retain imported build prerequisites and source documentation.\n' + '\n'.join(component['git_exceptions']) + '\n'
    if text == original:
        return data, False
    suffix = path.suffix.lower()
    if suffix in {".rs", ".go", ".js", ".ts", ".tsx", ".proto", ".c", ".h"}:
        prefix = "// "
    elif suffix in {".py", ".sh", ".toml", ".yaml", ".yml"} or path.name == "Dockerfile":
        prefix = "# "
    elif suffix == ".sql":
        prefix = "-- "
    else:
        prefix = None
    if prefix:
        lines = text.splitlines(keepends=True)
        offset = 1 if lines and lines[0].startswith("#!") else 0
        lines.insert(offset, prefix + MARK + "\n")
        text = "".join(lines)
    return text.encode("utf-8"), True


def prepare(component, refresh=False):
    role = component["role"]
    destination = ROOT / "engines" / role
    receipt_path = ROOT / "LICENSES" / role / "adaptations.json"
    if receipt_path.exists() and not refresh:
        print(f"{role}: already materialised", flush=True)
        return
    if refresh and receipt_path.exists():
        old = json.loads(receipt_path.read_text())
        for entry in old["files"]:
            path = ROOT / entry["path"]
            if not path.is_relative_to(destination) or not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != entry["sha256"]:
                raise SystemExit(f"Local change found in {path}; refusing to overwrite it.")
        for entry in old["files"]:
            (ROOT / entry["path"]).unlink()
    if destination.exists() and any(p.is_file() for p in destination.rglob("*")):
        raise SystemExit(f"{destination} already contains files; preserve local changes before re-importing.")
    CACHE.mkdir(parents=True, exist_ok=True)
    archive_path = CACHE / (component["revision"] + ".tar.gz")
    if not archive_path.exists():
        temporary = archive_path.with_suffix(".download")
        request = urllib.request.Request(component["archive"], headers={"User-Agent": "Avenra-source-preparation/1"})
        with urllib.request.urlopen(request, timeout=90) as source, temporary.open("wb") as target:
            while chunk := source.read(1024 * 1024):
                target.write(chunk)
        temporary.replace(archive_path)
    archive_hash = hashlib.sha256(archive_path.read_bytes()).hexdigest()
    licence_dir = receipt_path.parent
    licence_dir.mkdir(parents=True, exist_ok=True)
    records = []
    with tarfile.open(archive_path) as bundle:
        for member in bundle:
            path = PurePosixPath(member.name)
            parts = path.parts[1:]
            if not parts or any(p in SKIP for p in parts) or path.name in {"AGENTS.md", "CLAUDE.md"}:
                continue
            if any(p in {"..", ""} for p in parts) or member.issym() or member.islnk():
                continue
            if not member.isfile():
                continue
            relative = Path(*parts)
            original = bundle.extractfile(member).read()
            legal = relative.name in NOTICE_NAMES or relative.name.startswith(("LICENSE-", "LICENSE_", "APACHE_LICENSETEXT"))
            if legal:
                data, changed = original, False
                copy_path = licence_dir / "original" / relative
                copy_path.parent.mkdir(parents=True, exist_ok=True)
                copy_path.write_bytes(original)
            else:
                data, changed = adapt(original, relative, component)
            branded_parts = []
            for part in parts:
                for before, after in component["path_replacements"]:
                    suffix = '' if before.endswith(('-', '_')) else r'(?=$|[._-])'
                    part = re.sub(r"(?<![A-Za-z0-9])" + re.escape(before) + suffix, lambda _: after, part)
                branded_parts.append(part)
            target = destination.joinpath(*branded_parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(data)
            target.chmod(0o755 if member.mode & 0o111 else 0o644)
            records.append({"original_path": relative.as_posix(), "path": target.relative_to(ROOT).as_posix(),
                            "original_sha256": hashlib.sha256(original).hexdigest(),
                            "sha256": hashlib.sha256(data).hexdigest(), "modified": changed})
    receipt = {"role": role, "revision": component["revision"], "archive_sha256": archive_hash,
               "changes": "Product, namespace and local module branding only. Runtime behaviour requires separate verification.",
               "files": records}
    receipt_path.write_text(json.dumps(receipt, indent=2) + "\n")
    (licence_dir / "NOTICE.txt").write_text(
        "Avenra includes adapted source from " + component["repository"] + " at " + component["revision"] + ".\n"
        "Original terms and notices are retained in original/. Modified files and original hashes are recorded in adaptations.json.\n"
        "These modifications do not imply endorsement by the original authors.\n")
    print(f"{role}: {len(records)} files; {sum(r['modified'] for r in records)} adapted", flush=True)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("roles", nargs="*")
    parser.add_argument("--refresh", action="store_true")
    args = parser.parse_args()
    config = json.loads((ROOT / "LICENSES/components.json").read_text())
    for component in config["components"]:
        if not args.roles or component["role"] in args.roles:
            prepare(component, args.refresh)
