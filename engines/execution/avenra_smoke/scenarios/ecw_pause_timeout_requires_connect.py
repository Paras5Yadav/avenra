# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
"""ECW pause-on-timeout with explicit reconnect."""

import avenra_smoke as smoke

MARKER_PATH = "/tmp/avenra-smoke-pause-connect.txt"
MARKER = "avenra-smoke-pause-connect"


@smoke.scenario(
    "ecw.pause_timeout_requires_connect",
    sandbox=smoke.SandboxConfig(timeout=3),
)
def pause_timeout_requires_connect(test: smoke.Ecw) -> None:
    """A sandbox using the smoke fixture's default pause-on-timeout lifecycle
    remains unavailable until an explicit connect, then returns without losing
    its filesystem state.
    """

    with test.phase("write state before the timeout"):
        test.sandbox.files.write(MARKER_PATH, MARKER)

    with test.phase("wait for timeout pause"):
        test.wait_until_paused()

    with test.phase("verify explicit connect is required"):
        test.expect_paused_file_unavailable(MARKER_PATH)

    with test.phase("connect and verify preserved state"):
        test.reconnect_by_id()
        test.expect_file(MARKER_PATH, MARKER)
