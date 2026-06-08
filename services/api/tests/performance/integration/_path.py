from __future__ import annotations

import sys
from pathlib import Path

# Add the src directory so omni_modal packages can be imported
SRC_PATH = Path(__file__).resolve().parents[3] / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))
