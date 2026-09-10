"""Local-only by default; explicit bearer secret for a network deployment."""
import hmac
import ipaddress
import os
import hashlib
import time
from http.cookies import SimpleCookie
from urllib.parse import urlsplit


def session_cookie(secret):
    expiry = str(int(time.time()) + 8 * 3600)
    signature = hmac.new(secret.encode(), expiry.encode(), hashlib.sha256).hexdigest()
    return expiry + "." + signature


def allowed(scope, headers):
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
        except (KeyError, ValueError):
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
