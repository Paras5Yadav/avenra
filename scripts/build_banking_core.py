#!/usr/bin/env python3
"""Build the pinned banking source with a bounded JVM, then import its image."""
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
from services import docker

source = ROOT / 'engines/banking_core'
properties = (source / 'gradle.properties').read_text()
arguments = re.search(r'^org.gradle.jvmargs=(.*)$', properties, re.M).group(1)
arguments = re.sub(r'-Xmx\S+', '-Xmx1600m', arguments)
command = docker('run', '--rm', '--name', 'avenra-core-build', '--memory', '4g', '--cpus', '2', '-v', str(source) + ':/workspace', '-v', str(ROOT / '.local/gradle') + ':/root/.gradle', '-w', '/workspace', 'eclipse-temurin:21-jdk', './gradlew', '--no-daemon', '--max-workers=1', '-Dorg.gradle.jvmargs=' + arguments, '-x', 'test', '-x', 'spotlessJavaCheck', '-x', 'checkstyleMain', ':fineract-provider:jibBuildTar')
logs = ROOT / '.local/logs'
logs.mkdir(exist_ok=True)
with (logs / 'banking-core-build.log').open('wb') as log:
    result = subprocess.run(command, stdout=log, stderr=subprocess.STDOUT)
if result.returncode:
    raise SystemExit('Banking source build failed. Inspect .local/logs/banking-core-build.log; no runtime capability was enabled.')
artifact = source / 'fineract-provider/build/jib-image.tar'
if not artifact.is_file():
    raise SystemExit('Build completed without the expected image archive; runtime was not enabled.')
with artifact.open('rb') as handle:
    manifest = hashlib.file_digest(handle, 'sha256').hexdigest()
subprocess.run(docker('load', '--input', str(artifact)), check=True)
import tarfile
with tarfile.open(artifact) as archive:
    metadata = json.load(archive.extractfile('manifest.json'))
    tag = metadata[0]['RepoTags'][0]
subprocess.run(docker('tag', tag, 'avenra/banking-core:1.15.0'), check=True)
receipt = {'source_revision': 'd5636847ac556c30b437254c353f05526d172b97', 'archive_sha256': manifest, 'completed_at': time.time(), 'image': 'avenra/banking-core:1.15.0', 'scope': 'Source build only. Banking API qualification is recorded separately.', 'upstream_test_suite': 'not_run_by_this_build'}
(ROOT / '.local/banking-core-build.json').write_text(json.dumps(receipt, indent=2) + '\n')
print('Pinned banking source compiled and local image imported.')
