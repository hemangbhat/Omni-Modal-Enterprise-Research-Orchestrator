"""pytest bootstrap: put ``src`` on the path and make the suite hermetic.

Importing ``_path`` does both (see that module). unittest-style tests import
it themselves; this file covers pytest-style tests that do not.
"""

import _path  # noqa: F401
