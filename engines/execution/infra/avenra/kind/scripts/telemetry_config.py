#!/usr/bin/env python3
# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
"""Shared, non-secret Kind telemetry configuration for Tilt, bootstrap and shims."""

import argparse
import ipaddress
import json
import os
import re
import shlex
import socket
import subprocess
import urllib.parse
from pathlib import Path

OPTIONS = {
    "AVENRA_KIND_DATADOG_MODE",
    "AVENRA_KIND_DATADOG_UNBOUNDED",
    "AVENRA_KIND_DATADOG_SITE",
    "AVENRA_KIND_DATADOG_API_KEY_SECRET",
    "AVENRA_KIND_DATADOG_DURATION_MINUTES",
    "AVENRA_KIND_DATADOG_ACKNOWLEDGE_SHARED_BACKEND",
    "AVENRA_KIND_TELEMETRY_MODE",
    "AVENRA_KIND_CLUSTER_ID",
    "AVENRA_KIND_ENV",
    "AVENRA_KIND_VERSION",
    "AVENRA_KIND_PHASE",
    "AVENRA_KIND_OWNER",
    "AVENRA_KIND_SHIM_ENV",
    "AVENRA_KIND_SHIM_OTLP_ENDPOINT",
    "AVENRA_KIND_OTLP_METRICS_ENDPOINT",
    "AVENRA_KIND_OTLP_TRACES_ENDPOINT",
    "AVENRA_KIND_OBSERVABILITY",
}
PHASES = {"before-main", "after-datadog", "after-otel"}
# Each mode's (AVENRA_METRICS_SINK, AVENRA_TRACE_EXPORTER), for the Rust
# services and the Go shim alike.
MODES = {
    "datadog": ("datadog", "datadog"),
    "otel": ("otlp", "otlp"),
    "off": ("off", "off"),
}


def otlp_endpoint_rejection(endpoint):
    """Why an endpoint cannot be used, or None when it can.

    The rule the Rust services, the Go shim and the smoke runner apply, so an
    override accepted here is not refused later by the shim it configures.
    Duplicated rather than imported: these are separate entry points with no
    shared module. Keep it in step with `otlp_endpoint_rejection` in
    `scripts/avenra-cluster-smoke.py`.
    """
    if any(character.isspace() or character in ",\\{}" for character in endpoint):
        return "must not contain whitespace or Helm value separators"
    if "?" in endpoint or "#" in endpoint:
        return "must not include a query or fragment"
    try:
        parsed = urllib.parse.urlsplit(endpoint)
    except ValueError:
        return "must be a URL"
    if parsed.scheme != "http":
        return "must use the plaintext http:// scheme"
    if parsed.username is not None or parsed.password is not None:
        return "must not embed credentials"
    if not parsed.hostname:
        return "must include a host"
    _host, bracket, rest = parsed.netloc.rpartition("]")
    if bracket:
        port = rest[1:] if rest.startswith(":") else None
    else:
        _, separator, tail = parsed.netloc.rpartition(":")
        port = tail if separator else None
    if port is not None and not (
        port.isascii() and port.isdigit() and 1 <= int(port) <= 65535
    ):
        return "must use a port between 1 and 65535"
    try:
        _ = parsed.port
    except ValueError:
        return "must use a port between 1 and 65535"
    if parsed.path not in ("", "/"):
        return "must not include a path"
    return None


def options(environ=None):
    env = dict(os.environ if environ is None else environ)
    path = env.get("AVENRA_KIND_OBSERVABILITY_CONFIG")
    if path:
        document = json.loads(Path(path).read_text())
        if not isinstance(document, dict) or set(document) - OPTIONS:
            raise ValueError(
                "observability config must contain only supported AVENRA_KIND options"
            )
        if any(not isinstance(value, str) for value in document.values()):
            raise ValueError("observability config values must be strings")
        # A watched config file is authoritative, including for stop-export;
        # an older environment must not turn SaaS back on after file rollback.
        env.update(document)
    return env


def default_environment():
    """Per-devbox identity, matching bootstrap.sh's pre-selector behaviour.

    The documented Datadog filter is `env:local-*` for all local-dev traffic and
    `env:local-<host>` for one devbox, so an ordinary bootstrap that supplies no
    experiment identity must not collapse every developer onto one shared env.
    """
    host = re.sub(r"[^a-z0-9-]", "-", socket.gethostname().lower()).strip("-")
    return f"local-{host}"[:128] if host else "local-unknown"


def checkout_commit():
    """The checkout's commit, Kind's one version source, or "" outside Git.

    Workloads report it as service.version unless an experiment names its own
    version, and every image built for the bench records it as its build SHA,
    which host sidecars such as net-gateway inherit from the worker image.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(Path(__file__).resolve().parent)]
            + ["rev-parse", "--short=12", "HEAD"],
            capture_output=True,
            check=True,
            text=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return ""
    return result.stdout.strip()


def token(value, name, *, empty=False):
    if empty and not value:
        return value
    if not re.fullmatch(r"[a-z0-9][a-z0-9._-]{0,127}", value):
        raise ValueError(
            f"{name} must be a lowercase telemetry identifier (max 128 characters)"
        )
    return value


def configuration(env):
    kind_cluster = env.get("AVENRA_KIND_CLUSTER", "avenra-local")
    if not re.fullmatch(r"(?:avenra-local(?:-[a-z0-9]+(?:-[a-z0-9]+)*)?|avenra-oss-[0-9a-f]{10})", kind_cluster):
        raise ValueError(
            "AVENRA_KIND_CLUSTER must name a avenra-local or avenra-oss-<10 hex digits> Kind bench"
        )
    cluster_id = env.get("AVENRA_KIND_CLUSTER_ID", "kind-local")
    if not re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", cluster_id):
        raise ValueError(
            "AVENRA_KIND_CLUSTER_ID must be a lowercase DNS label (max 63 characters)"
        )
    # Exporter selection does not authorize installation of a SaaS Agent.
    mode = env.get("AVENRA_KIND_TELEMETRY_MODE", "datadog")
    if mode not in MODES:
        raise ValueError("AVENRA_KIND_TELEMETRY_MODE must be otel, datadog, or off")
    environment = token(
        env.get("AVENRA_KIND_ENV")
        or env.get("AVENRA_KIND_SHIM_ENV")
        or default_environment(),
        "AVENRA_KIND_ENV",
    )
    revision = token(
        env.get("AVENRA_KIND_VERSION", ""), "AVENRA_KIND_VERSION", empty=True
    )
    phase = env.get("AVENRA_KIND_PHASE", "")
    if phase and phase not in PHASES:
        raise ValueError("AVENRA_KIND_PHASE must name a supported before/after phase")
    owner = token(env.get("AVENRA_KIND_OWNER", ""), "AVENRA_KIND_OWNER", empty=True)
    endpoint = env.get("AVENRA_KIND_SHIM_OTLP_ENDPOINT")
    if endpoint is None and mode == "datadog":
        endpoint = "http://127.0.0.1:4317"
    if endpoint is not None:
        rejection = otlp_endpoint_rejection(endpoint)
        if rejection:
            raise ValueError(f"AVENRA_KIND_SHIM_OTLP_ENDPOINT {rejection}")
    endpoints = {}
    for signal in ("metrics", "traces"):
        key = f"AVENRA_KIND_OTLP_{signal.upper()}_ENDPOINT"
        value = env.get(key)
        if value is not None:
            rejection = otlp_endpoint_rejection(value)
            if rejection:
                raise ValueError(f"{key} {rejection}")
        legacy = env.get("AVENRA_KIND_SHIM_OTLP_ENDPOINT")
        if legacy is not None and value is not None:
            raise ValueError(
                f"{key} cannot be combined with node-only AVENRA_KIND_SHIM_OTLP_ENDPOINT; "
                "remove the legacy override to configure Pods and host processes together"
            )
        # Preserve the legacy node-reachable receiver, including node loopback.
        # Only the new global options also change the Pod-DNS receiver route.
        endpoints[signal] = value
        if mode == "otel" and endpoints[signal]:
            host = urllib.parse.urlsplit(endpoints[signal]).hostname
            try:
                loopback = ipaddress.ip_address(host).is_loopback
            except ValueError:
                loopback = host.lower() == "localhost"
            if loopback:
                raise ValueError(
                    f"{key} must be reachable from both Pods and Kind nodes; "
                    "a loopback receiver is not shared"
                )
    version = f"{revision}-{phase}" if revision and phase else revision
    commit = checkout_commit()
    metrics, traces = MODES[mode]
    observability = env.get("AVENRA_KIND_OBSERVABILITY", "auto")
    if observability not in {"auto", "off"}:
        raise ValueError("AVENRA_KIND_OBSERVABILITY must be auto or off")
    config = {
        "mode": mode,
        "observability": observability,
        "install_observability": observability == "auto" and mode == "otel",
        "endpoints": endpoints,
        "local_signals": [signal for signal, endpoint in endpoints.items() if not endpoint],
        "log_route": "alloy" if mode == "otel" else "datadog",
        "kind_cluster": kind_cluster,
        "cluster_id": cluster_id,
        "environment": environment,
        "revision": revision,
        "phase": phase,
        "owner": owner,
        "version": version,
        "commit": commit,
        "shim_endpoint": endpoint,
        "helm_set": [
            f"telemetry.metricsSink={metrics}",
            f"telemetry.traceExporter={traces}",
            f"telemetry.traceSampler={'always_on' if mode == 'otel' else ''}",
            # The local Collector's delta-to-cumulative conversion forgets a
            # stream after two idle minutes, so a rare counter would restart
            # from its latest increment. Cumulative Rust exports carry their
            # own running totals; delta stays the default for Datadog, whose
            # OTLP intake accepts only delta.
            f"telemetry.otlpMetricsTemporality={'cumulative' if mode == 'otel' else ''}",
            f"telemetry.excludeDatadogLogs={'true' if mode == 'otel' else 'false'}",
            *[
                f"telemetry.otlp{signal.title()}Endpoint={value}"
                for signal, value in endpoints.items() if value is not None
            ],
            f"datadogEnvironment={environment}",
            f"telemetry.version={version or commit}",
            f"telemetry.phase={phase}",
            f"telemetry.owner={owner}",
            f"telemetry.cluster={kind_cluster}",
            f"clusterId={cluster_id}",
        ],
    }
    # The host runtime-supervisor's node environment (avenra-node-manager-local)
    # and sandbox-manager's host sidecars need node-reachable Collectors, as
    # the shim does.
    config["helm_set"] += [
        f"telemetry.nodeOtlpMetricsEndpoint={collector_endpoint(config, 'metrics')}",
        f"telemetry.nodeOtlpTracesEndpoint={collector_endpoint(config, 'traces')}",
    ]
    return config


# Each signal has its own Collector Service. containerd runs the shim, and
# systemd the runtime-supervisor, on the Kind node, which has no cluster DNS, so
# both name each Service by the fixed ClusterIP that chart's profiles/kind.yaml
# pins as collector.clusterIP; change the two together. Because it is fixed
# rather than looked up, the shim wrapper and the supervisor's node environment
# can be written before the Collectors exist and stay right when they are
# reinstalled.
NODE_COLLECTORS = {"metrics": "10.96.0.20", "traces": "10.96.0.21"}


def collector_endpoint(config, signal):
    """A node-reachable OTLP endpoint for one signal's Collector."""
    if config["endpoints"][signal] is not None:
        return config["endpoints"][signal]
    if config["shim_endpoint"] is not None:
        return config["shim_endpoint"]
    return f"http://{NODE_COLLECTORS[signal]}:4317"


def shim_endpoint(config):
    return collector_endpoint(config, "traces")


def exports_otlp(config):
    """Whether the shim sends either signal over OTLP in this mode."""
    return "otlp" in MODES[config["mode"]]


def shim_wrapper(config, binary, fallback_version):
    version = config["version"] or config["commit"] or fallback_version
    metrics, traces = MODES[config["mode"]]
    values = {
        "AVENRA_CLUSTER_ID": config["cluster_id"],
        "AVENRA_TRACE_EXPORTER": traces,
        "DD_AGENT_HOST": "127.0.0.1",
        "DD_ENV": config["environment"],
        "DD_SERVICE": "containerd-shim-avenra-v2",
        "DD_VERSION": version,
        # The shim reads AVENRA_METRICS_SINK too. Left unset it defaults to
        # auto -> datadog, which would keep the shim on native metrics while
        # every workload moved to OTLP: wrong, silently.
        "AVENRA_METRICS_SINK": metrics,
    }
    # `off` exports nothing, so it names no receiver either.
    if traces != "off":
        values["OTEL_EXPORTER_OTLP_TRACES_ENDPOINT"] = shim_endpoint(config)
        values["OTEL_EXPORTER_OTLP_TRACES_PROTOCOL"] = "grpc"
    if exports_otlp(config):
        # The Avenra and OTel identity settings outrank the DD_* ones, so
        # setting them in a Datadog-only mode would change the tags this shim
        # emits: its DogStatsD version tag carries the shim's build version,
        # and AVENRA_VERSION would replace it with the wrapper's. Only a mode
        # that actually exports OTLP needs them.
        values["AVENRA_ENV"] = config["environment"]
        values["AVENRA_VERSION"] = version
        values["OTEL_SERVICE_NAME"] = "containerd-shim-avenra-v2"
    if metrics == "otlp":
        # Shim metrics have no endpoint fallback, unlike traces: without these
        # the shim fails to start rather than guessing a port.
        values["OTEL_EXPORTER_OTLP_METRICS_ENDPOINT"] = collector_endpoint(config, "metrics")
        values["OTEL_EXPORTER_OTLP_METRICS_PROTOCOL"] = "grpc"
    if config["mode"] == "otel":
        values["OTEL_TRACES_SAMPLER"] = "always_on"
    tags = f"kind_cluster:{config['kind_cluster']}"
    # Identity fields, which the resolver ranks above DD_*, and cluster
    # metadata, which it does not read as identity at all. Gating the direct
    # AVENRA_* settings alone would not be enough: the resolver reads
    # service.version from here into the same slot, so a Datadog-only mode
    # would still replace the shim's build version in its DogStatsD tag.
    identity = [
        f"deployment.environment.name={config['environment']}",
        f"service.version={version}",
    ]
    metadata = [f"k8s.cluster.name={config['kind_cluster']}"]
    for key in ("owner", "phase"):
        if config[key]:
            tags += f",kind_{key}:{config[key]}"
            metadata.append(f"kind.{key}={config[key]}")
    resource = ",".join((identity if exports_otlp(config) else []) + metadata)
    values["DD_TAGS"] = tags
    lines = ["#!/usr/bin/env bash", "set -euo pipefail"]
    lines.extend(f"export {key}={shlex.quote(value)}" for key, value in values.items())
    # The resolver computes the process-local attributes itself and drops any it
    # inherits through OTEL_RESOURCE_ATTRIBUTES, so one shared value cannot make
    # two processes claim the same identity. The node name therefore has to
    # arrive as its own setting or it is lost; host.name is recomputed from
    # HOSTNAME, which is the node here because the shim runs outside a pod.
    lines.append('export AVENRA_K8S_NODE_NAME="$(hostname)"')
    lines.append(
        f'export OTEL_RESOURCE_ATTRIBUTES={shlex.quote(resource)}",host.name=$(hostname),k8s.node.name=$(hostname)"'
    )
    lines.append(f'exec {shlex.quote(binary)} "$@"')
    return "\n".join(lines) + "\n"


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("json", "helm-set", "shim-wrapper", "commit"))
    parser.add_argument(
        "--binary", default="/usr/local/lib/avenra/containerd-shim-avenra-v2"
    )
    parser.add_argument("--version", default="local")
    args = parser.parse_args()
    try:
        config = configuration(options())
        if args.command == "json":
            print(json.dumps(config))
        elif args.command == "helm-set":
            print("\n".join(config["helm_set"]))
        elif args.command == "commit":
            print(config["commit"])
        else:
            print(shim_wrapper(config, args.binary, args.version), end="")
    except (ValueError, OSError) as error:
        parser.exit(2, f"Kind telemetry configuration: {error}\n")


if __name__ == "__main__":
    main()
