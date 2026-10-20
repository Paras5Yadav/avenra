# Modified for Avenra. Original attribution and adaptation record: LICENSES/.
"""ECW filesystem happy path."""

import avenra_smoke as smoke

PATH = "/tmp/avenra-smoke/filesystem-round-trip.txt"
CONTENT = "avenra-smoke-filesystem-ok"


@smoke.scenario("ecw.filesystem_round_trip")
def filesystem_round_trip(test: smoke.Ecw) -> None:
    """Text written through the ECW Files API can be read back exactly."""

    with test.phase("write and read marker file"):
        test.sandbox.files.write(PATH, CONTENT)
        observed = test.sandbox.files.read(PATH)
        test.verify(
            observed == CONTENT,
            "ECW file read did not return the content that was written",
            path=PATH,
            expected_content=CONTENT,
            observed_content=observed,
        )
