"""pytest bootstrap: put ``src`` on the path and make the suite hermetic.

Importing ``_path`` does both (see that module). unittest-style tests import
it themselves; this file covers pytest-style tests that do not.
"""

import _path  # noqa: F401

try:
    from hypothesis import HealthCheck, settings
except ImportError:  # pragma: no cover - hypothesis is optional
    pass
else:
    # Property tests here check correctness, not speed. Hypothesis's per-example
    # deadline (200 ms) and its input-generation timing check fail at random on
    # a busy machine or a cold interpreter, so both are off for the whole suite.
    settings.register_profile(
        "omero", deadline=None, suppress_health_check=[HealthCheck.too_slow]
    )
    settings.load_profile("omero")
