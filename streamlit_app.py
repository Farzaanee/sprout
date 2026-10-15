"""Streamlit entry point (also what Streamlit Community Cloud runs)."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "src"))

from sprout.app.dashboard import main  # noqa: E402

main()
