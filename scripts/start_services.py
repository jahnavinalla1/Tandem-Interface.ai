"""Dev-convenience shim: `uv run python scripts/start_services.py` still works.

The actual launcher lives in `tandem.cli` so it ships as part of the installed
package and is reachable via the `tandem` console-script entrypoint even when this
repository's `scripts/` directory is not present (e.g. after `pip install` from a
built wheel).
"""

import sys

from tandem.cli import main

if __name__ == "__main__":
    sys.exit(main() or 0)
