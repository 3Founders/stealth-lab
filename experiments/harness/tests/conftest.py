import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

FIXTURES = HERE.parent / "fixtures"
MICRO_FIXTURES = HERE.parent / "fixtures" / "micro"
ERROR_FLOOR_FIXTURES = HERE.parent / "fixtures" / "error_floor"
