"""Generate route documentation without opening a database or starting workers."""
import os
import sys
from pathlib import Path

root = Path(__file__).resolve().parents[3]
sys.path.insert(0,str(root / "services/api"))
os.environ["FIELD_SCHEMA_EXPORT"] = "1"
from app.main import app
import yaml

schema = app.openapi()
schema["info"]["description"] = "Field Inspect offline inspection API. Local loopback access by default; configured credentials required for network access. Processing completion is not field acceptance. No vehicle control or online package-upload endpoint. New functions have not been tested."
schema["servers"] = [{"url":"/"}]
schema.setdefault("components",{})["securitySchemes"] = {
    "BearerAuth":{"type":"http","scheme":"bearer"},
    "OperatorSession":{"type":"apiKey","in":"cookie","name":"astra_session"},
}
schema["security"] = [{"BearerAuth":[]},{"OperatorSession":[]}]
schema["paths"]["/api/health"]["get"]["security"] = []
(root / "packages/contracts/openapi.yaml").write_text(yaml.safe_dump(schema,allow_unicode=True,sort_keys=False),encoding="utf-8",newline="\n")
print("Generated packages/contracts/openapi.yaml; no requests or workers executed")
