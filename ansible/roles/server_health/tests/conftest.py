import sys
from pathlib import Path

# probe.py / responder.py are deployed via `copy` (not `template`) and are
# deliberately host-agnostic — import them directly from ../files so the
# unit tests exercise exactly what ships to every host.
FILES_DIR = Path(__file__).resolve().parent.parent / "files"
if str(FILES_DIR) not in sys.path:
    sys.path.insert(0, str(FILES_DIR))
