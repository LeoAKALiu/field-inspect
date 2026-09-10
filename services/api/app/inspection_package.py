"""Compatibility import for the shared edge/server v2 package contract."""
from pathlib import Path
import sys

_CONTRACT_ROOT = Path(__file__).resolve().parents[3] / "packages/contracts/python"
if str(_CONTRACT_ROOT) not in sys.path:
    sys.path.insert(0, str(_CONTRACT_ROOT))
from astra_inspect_contract.package import *  # noqa: F401,F403,E402
