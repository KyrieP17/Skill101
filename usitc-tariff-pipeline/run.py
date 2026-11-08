"""Small, discoverable entry point for the package CLI.

Keeping this wrapper at project root lets a new analyst run the pipeline without
installing the package first.  The parser lives in ``usitc_pipeline.cli``; scope
validation and query planning there need no pandas.
"""

from pathlib import Path
import sys


PROJECT_ROOT = Path(__file__).resolve().parent
SRC = PROJECT_ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from usitc_pipeline.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())

