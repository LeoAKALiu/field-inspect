"""Read-only import ledger. Import is performed only by the server-local CLI."""

from fastapi import APIRouter, Depends

from ..db import Database
from ..run_bundle import list_run_bundle_imports
from . import get_db

router = APIRouter()


@router.get("/imports")
def list_imports(db: Database = Depends(get_db)) -> list[dict]:
    """Safe status projection; never returns archive paths."""
    return list_run_bundle_imports(db)
