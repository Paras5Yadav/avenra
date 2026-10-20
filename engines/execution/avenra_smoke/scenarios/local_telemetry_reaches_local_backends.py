# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
"""In `otel` mode the stack's metrics, traces and logs reach the local backends."""

import avenra_smoke as smoke

MARKER = "avenra-smoke-telemetry-ok"


# The reads poll for this scenario's own sandbox; Tempo's 30 s search cutoff
# sets the floor (see avenra_smoke/telemetry_delivery.py).
@smoke.scenario("local.telemetry_reaches_local_backends", telemetry_mode="otel")
def telemetry_reaches_local_backends(test: smoke.KindEcw) -> None:
    """Once a sandbox has run a command, Tempo returns one trace in which
    avenra-api created it and containerd-shim-avenra-v2 started it, Prometheus
    holds create latency from that shim and from avenra-api since the
    scenario began, and Loki holds a Avenra pod log line naming the sandbox."""

    with test.phase("run a command in the sandbox"):
        result = test.run_command(f"printf %s {MARKER}")
    test.expect_command(result, stdout=MARKER)

    telemetry = test.telemetry
    with test.phase("Tempo: one trace from avenra-api to the shim for this sandbox"):
        telemetry.expect_traces()

    with test.phase("Prometheus: create latency from this sandbox's shim and the API"):
        telemetry.expect_metrics()

    with test.phase("Loki: a Avenra pod log line names the sandbox"):
        telemetry.expect_logs()
