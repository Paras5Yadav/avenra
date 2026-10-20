# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
"""Render kind charts for the repository guards that read them.

Used by `test_kind_known_egress_chart.py`, which asserts the sandbox-manager
DaemonSet's rendered env. Not a test module: nothing here asserts a chart
value.
"""

from __future__ import annotations

import os
import shlex
import shutil
import subprocess
from pathlib import Path

import yaml

REPO_ROOT = Path(__file__).resolve().parents[2]
KIND_DIR = REPO_ROOT / "infra/avenra/kind"
SANDBOX_MANAGER_CHART = KIND_DIR / "charts/avenra-sandbox-manager-local"
API_CHART = KIND_DIR / "charts/avenra-api-local"
CLUSTER_CONTROLLER_CHART = KIND_DIR / "charts/avenra-cluster-controller-local"
HELM_BIN = shutil.which("helm")
TELEMETRY_CHARTS = tuple(
    KIND_DIR / "charts" / name
    for name in (
        "avenra-api-local",
        "avenra-internal-api-local",
        "avenra-credmgr-authority-local",
        "avenra-data-plane-local",
        "avenra-cluster-controller-local",
        "avenra-node-manager-local",
        "avenra-runtime-manager-local",
        "avenra-volume-manager-local",
        "avenra-credential-manager-local",
        "avenra-template-manager-local",
        "avenra-sandbox-manager-local",
        "avenra-node-ctl-local",
        "avenra-node-api-local",
        # Not avenra-build-manager-local: its traces are deliberately off in
        # every mode (`traceExporter` in its values.yaml), which
        # test_kind_telemetry_config.py checks separately.
    )
)
BUILD_MANAGER_CHART = KIND_DIR / "charts/avenra-build-manager-local"


def render(chart: Path, release: str, *extra: str) -> list[dict]:
    if HELM_BIN is None:
        raise AssertionError("helm is required to render kind charts")
    manifest = subprocess.run(
        [
            HELM_BIN,
            "template",
            release,
            str(chart),
            "--namespace",
            "avenra",
            *extra,
        ],
        cwd=REPO_ROOT,
        check=True,
        text=True,
        capture_output=True,
    ).stdout
    return [document for document in yaml.safe_load_all(manifest) if document]


def object_named(documents: list[dict], kind: str, name: str) -> dict:
    for document in documents:
        if (
            document.get("kind") == kind
            and document.get("metadata", {}).get("name") == name
        ):
            return document
    raise AssertionError(f"render has no {kind}/{name}")


def env_by_name(workload: dict) -> dict[str, dict]:
    container = workload["spec"]["template"]["spec"]["containers"][0]
    return {entry["name"]: entry for entry in container["env"]}


def stub_command(commands: Path, name: str, body: str) -> Path:
    commands.mkdir(exist_ok=True)
    stub = commands / name
    stub.write_text(f"#!/bin/sh\nset -eu\n{body}\n")
    stub.chmod(0o755)
    return stub


def bootstrap_guard_environment(tmp_path: Path) -> dict[str, str]:
    """Keep invalid-config probes away from the host's container/cluster tools."""
    home = tmp_path / "home"
    home.mkdir()
    commands = tmp_path / "bin"
    commands.mkdir()
    marker = shlex.quote(str(tmp_path / "unexpected-operation"))
    for name in ("docker", "kind", "kubectl", "helm", "openssl"):
        stub_command(commands, name, f"printf '%s\\n' {name} >> {marker}\nexit 99")
    return {"HOME": str(home), "PATH": f"{commands}:{os.environ['PATH']}"}
