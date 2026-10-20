# Local metrics backend

Install Collector Contrib and Prometheus on the verified `kind-avenra-local`
bench (`kind-$AVENRA_KIND_CLUSTER` when `AVENRA_KIND_CLUSTER` is set). The
helper checks cluster identity and namespace ownership. Shared
installations use the [observability deployment runbook](../../observability/README.md).

Kind bootstrap and Tilt install this backend automatically
in `otel` mode unless an explicit metrics receiver is supplied or backend
installation is disabled. Use this helper to manage the release independently.

## Deploy and query

Run from the repository root:

```bash
helm dependency build infra/avenra/observability/charts/metrics
helm lint infra/avenra/observability/charts/metrics \
  -f infra/avenra/observability/charts/metrics/profiles/kind.yaml
bash infra/avenra/kind/metrics/local.sh up
bash infra/avenra/kind/metrics/local.sh status
# Default bootstrap and full-stack Tilt already forward this port in otel mode.
bash infra/avenra/kind/metrics/local.sh ui
# Prometheus: http://127.0.0.1:19090; Ctrl-C closes the tunnel.
```

The release owns only the backend, configuration, storage and access policies.
Installing it creates no producer Jobs and changes no Avenra sink. Grafana is an
independent release, also installed by the `otel` profile but not required for ingestion.
Kind uses disposable local storage and does not provide enforced network
isolation, authentication or production sizing.

## Validate

The [metrics integration workflow](../../../../scripts/tests/observability/metrics/README.md)
owns the synthetic producer, capture, fault injection and assessment. It creates
and cleans up its own Job without upgrading this release.

## Upgrade and retire

Use the helper's `up` command for upgrades. When moving from the earlier chart
that owned a synthetic workload, archive its evidence first: Helm removes that
old release-owned Job. New validation Jobs have an independent lifecycle.

To retire only this owned local namespace and its disposable TSDB:

```bash
bash infra/avenra/kind/metrics/local.sh cleanup --confirm-delete-local-data
```

This deletes local data. It leaves saved evidence, the Kind cluster, Avenra,
Datadog and other observability releases intact.
