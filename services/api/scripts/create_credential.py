"""Provision a local high-entropy credential; never print credential material."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import secrets

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("--file",type=Path,required=True)
parser.add_argument("--token-file",type=Path,required=True)
parser.add_argument("--id",required=True)
parser.add_argument("--role",choices=["viewer","reviewer","operator"],required=True)
args=parser.parse_args()
if not re.fullmatch(r"[A-Za-z0-9._-]{1,64}",args.id):
    parser.error("invalid identity")
registry=args.file.resolve(); destination=args.token_file.resolve()
if registry==destination or not registry.is_absolute():
    parser.error("credential registry and token output must differ")
rows=json.loads(registry.read_text(encoding="utf-8")) if registry.exists() else []
if not isinstance(rows,list) or any(row.get("id")==args.id for row in rows):
    parser.error("existing identity: remove/rotate deliberately, do not overwrite")
if len(rows)>=100:
    parser.error("credential registry exceeds 100 identities")
secret=secrets.token_urlsafe(48)
rows.append({"id":args.id,"role":args.role,"token_sha256":hashlib.sha256(secret.encode()).hexdigest()})
destination.parent.mkdir(parents=True,exist_ok=True)
registry.parent.mkdir(parents=True,exist_ok=True)
# Exclusive output creation avoids clobbering another operator's token.
with destination.open("x",encoding="utf-8") as output:
    output.write(secret+"\n")
if os.name!="nt":
    destination.chmod(0o600)
temporary=registry.with_name(registry.name+"."+secrets.token_hex(6)+".tmp")
with temporary.open("x",encoding="utf-8") as output:
    json.dump(rows,output,indent=2);output.write("\n");output.flush();os.fsync(output.fileno())
if os.name!="nt":temporary.chmod(0o600)
os.replace(temporary,registry)
print("Credential written to the requested private files; no token printed. Restrict file permissions to deployment administrators.")
