from pathlib import Path
from typing import Final

REPO_ROOT: Final = Path(__file__).resolve().parents[3]
EXAMPLES: Final = REPO_ROOT / "contracts" / "examples"
SERVICE_TOKEN: Final = "t" * 32
