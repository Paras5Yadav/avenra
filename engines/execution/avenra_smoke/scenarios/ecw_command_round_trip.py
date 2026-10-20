# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
"""The smallest useful public-client smoke scenario."""

import avenra_smoke as smoke

MARKER = "avenra-smoke-command-ok"


@smoke.scenario("ecw.command_round_trip", api_surfaces=("ecw", "native"))
def command_round_trip(test: smoke.CommandSandboxFixture) -> None:
    """A sandbox can execute a command through the selected public data plane
    and return its exact output.
    """

    with test.phase("run marker command"):
        result = test.run_command(f"printf %s {MARKER}")

    test.expect_command(result, stdout=MARKER)
