"""Local-only by default; explicit bearer secret for a network deployment."""
import hmac
import ipaddress
import os
import hashlib
import time
from http.cookies import SimpleCookie, CookieError
from urllib.parse import urlsplit


def session_cookie(secret):
    expiry = str(int(time.time()) + 8 * 3600)
    signature = hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return expiry + "." + signature


def _legacy_allowed(scope, headers):
    secret = os.environ.get("ASTRA_API_TOKEN", "")
    if secret:
        authorization = headers.get("authorization", "")
        if hmac.compare_digest(authorization.encode(), ("Bearer " + secret).encode()):
            return True
        try:
            cookie = SimpleCookie(); cookie.load(headers.get("cookie", ""))
            expiry, signature = cookie["astra_session"].value.split(".")
            expected = hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
            if not int(time.time()) < int(expiry) <= int(time.time()) + 8*3600:
                return False
            origin = headers.get("origin")
            if origin and urlsplit(origin).netloc != headers.get("host"):
                return False
            # Cookie-authorized writes and WebSockets must carry a same-origin Origin.
            if (scope.get("type") == "websocket" or scope.get("method") not in {"GET", "HEAD"}) and not origin:
                return False
            return hmac.compare_digest(signature, expected)
        except (KeyError, ValueError, CookieError):
            return False
    # A reverse proxy must use authentication; its local address isn't a user's identity.
    if any(key in headers for key in ("forwarded", "x-forwarded-for", "x-real-ip")):
        return False
    origin = headers.get("origin")
    if origin:
        try:
            hostname = urlsplit(origin).hostname
            if hostname != "localhost" and not ipaddress.ip_address(hostname).is_loopback:
                return False
        except ValueError:
            return False
    client = scope.get("client")
    try:
        return client is not None and ipaddress.ip_address(client[0]).is_loopback
    except ValueError:
        return False


# Optional individually revocable credentials. Configuration errors fail closed.
import json
import re
from pathlib import Path
from contextvars import ContextVar

_current_identity = ContextVar("field_identity", default=None)
SCHEMA = """CREATE TABLE IF NOT EXISTS access_audit (
 seq INTEGER PRIMARY KEY AUTOINCREMENT, actor TEXT NOT NULL, role TEXT NOT NULL,
 method TEXT NOT NULL, path TEXT NOT NULL, status INTEGER NOT NULL, created_at TEXT NOT NULL
);"""


def users():
    raw = json.loads(Path(os.environ["FIELD_AUTH_FILE"]).read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not 1 <= len(raw) <= 100:
        raise ValueError("credential file requires 1-100 users")
    seen = set(); tokens = set()
    for user in raw:
        if set(user) != {"id", "role", "token_sha256"} or not re.fullmatch(r"[A-Za-z0-9._-]{1,64}",user["id"]):
            raise ValueError("invalid credential entry")
        if user["role"] not in {"viewer","reviewer","operator"} or not re.fullmatch(r"[a-f0-9]{64}",user["token_sha256"]):
            raise ValueError("invalid role or token digest")
        if user["id"] in seen or user["token_sha256"] in tokens:
            raise ValueError("duplicate credential identity")
        seen.add(user["id"]); tokens.add(user["token_sha256"])
    return raw


def identity(scope, headers):
    if not os.environ.get("FIELD_AUTH_FILE"):
        if _legacy_allowed(scope,headers):
            return {"id":"shared_operator" if os.environ.get("ASTRA_API_TOKEN") else "local_operator","role":"operator","mode":"shared"}
        return None
    try:
        accounts = users()
        auth = headers.get("authorization","")
        if auth.startswith("Bearer "):
            digest = hashlib.sha256(auth[7:].encode()).hexdigest()
            for account in accounts:
                if hmac.compare_digest(digest,account["token_sha256"]):
                    return {"id":account["id"],"role":account["role"],"mode":"individual"}
        cookie = SimpleCookie(); cookie.load(headers.get("cookie",""))
        user_id, expiry, signature = cookie["astra_session"].value.split(":")
        if not int(time.time()) < int(expiry) <= int(time.time())+8*3600:
            return None
        origin = headers.get("origin")
        if origin and urlsplit(origin).netloc != headers.get("host"):
            return None
        if (scope.get("type")=="websocket" or scope.get("method") not in {"GET","HEAD"}) and not origin:
            return None
        for account in accounts:
            if account["id"] == user_id:
                expected = hmac.new(bytes.fromhex(account["token_sha256"]),f"{user_id}:{expiry}".encode(),hashlib.sha256).hexdigest()
                if hmac.compare_digest(signature,expected):
                    return {"id":user_id,"role":account["role"],"mode":"individual"}
    except (OSError,ValueError,TypeError,KeyError,AttributeError,CookieError):
        return None
    return None


def permitted(principal, scope):
    if principal is None:
        return False
    if scope.get("path") == "/api/access/audit" and principal["role"] != "operator":
        return False
    if scope.get("type")=="websocket" or scope.get("method") in {"GET","HEAD","OPTIONS"}:
        return True
    path = scope.get("path","")
    if path in {"/api/access/login","/api/access/logout"}:
        return True
    if principal["role"] == "operator":
        return True
    return principal["role"] == "reviewer" and scope.get("method") == "POST" and (
        path in {"/api/instruments/reviews","/api/instruments/matches"} or
        re.fullmatch(r"/api/instruments/observations/[^/]+/match",path) is not None)


def allowed(scope, headers):
    return permitted(identity(scope,headers),scope)


def login_cookie(principal):
    if principal["mode"] != "individual":
        secret=os.environ.get("ASTRA_API_TOKEN","")
        return session_cookie(secret) if secret else None
    account=next(u for u in users() if u["id"]==principal["id"])
    expiry=str(int(time.time())+8*3600)
    text=f"{account['id']}:{expiry}"
    return text+":"+hmac.new(bytes.fromhex(account["token_sha256"]),text.encode(),hashlib.sha256).hexdigest()


def audit_actor(fallback):
    principal=_current_identity.get()
    return principal["id"] if principal and principal["mode"]=="individual" else fallback
