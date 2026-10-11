from __future__ import annotations

import os
import sys
from pathlib import Path

SRC_PATH = Path(__file__).resolve().parents[1] / "src"

if str(SRC_PATH) not in sys.path:
    sys.path.insert(0, str(SRC_PATH))

# Keep the suite hermetic: never let code under test discover the developer's
# .env, which holds real credentials (LLM, email, billing, storage, database).
os.environ["OMERO_DISABLE_DOTENV"] = "1"
