"""pytest configuration shared across the DPSpice test suite."""
import os
import sys

# Make the sibling helper modules (golden_cases) importable as top-level names
# regardless of where pytest is invoked from.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))


def pytest_addoption(parser):
    parser.addoption(
        "--update-golden",
        action="store_true",
        default=False,
        help="Re-freeze golden_reference.json from the current engine output. "
             "Use ONLY when a value change is intended.",
    )
