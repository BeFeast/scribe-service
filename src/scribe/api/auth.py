"""Request owner attribution and operator auth policy helpers."""

from __future__ import annotations

import base64
import binascii
import ipaddress
import json
import secrets
from dataclasses import dataclass
from enum import StrEnum

from fastapi import HTTPException, Request

from scribe.config import settings


class AuthState(StrEnum):
    public = "public"
    trusted_lan = "trusted_lan"
    machine_bearer = "machine_bearer"


@dataclass(frozen=True)
class OwnerIdentity:
    subject: str
    email: str | None = None
    display_name: str | None = None


def default_owner() -> OwnerIdentity | None:
    subject = settings.default_owner_subject.strip() or settings.default_owner_email.strip()
    if not subject:
        return None
    return OwnerIdentity(
        subject=subject,
        email=settings.default_owner_email.strip() or None,
        display_name=None,
    )


def current_owner(request: Request) -> OwnerIdentity | None:
    bearer = _bearer_token(request)
    if bearer:
        machine_token = settings.machine_bearer_token.strip()
        if machine_token and secrets.compare_digest(bearer, machine_token):
            return default_owner()
        clerk_owner = _owner_from_clerk_jwt(bearer)
        if clerk_owner is not None:
            return clerk_owner

    if _is_trusted_lan_request(request):
        return default_owner()
    return None


def classify_auth(request: Request) -> AuthState:
    bearer = _bearer_token(request, strict=True)
    if bearer:
        machine_token = settings.machine_bearer_token.strip()
        if machine_token and secrets.compare_digest(bearer, machine_token):
            return AuthState.machine_bearer
    if _is_trusted_lan_request(request):
        return AuthState.trusted_lan
    return AuthState.public


def require_operator_auth(request: Request) -> AuthState:
    state = classify_auth(request)
    if state == AuthState.public:
        raise HTTPException(status_code=401, detail="trusted LAN or bearer token required")
    return state


def _bearer_token(request: Request, *, strict: bool = False) -> str | None:
    header = request.headers.get("authorization")
    if header is None:
        return None
    parts = header.split()
    if len(parts) != 2 or parts[0].lower() != "bearer":
        if strict:
            raise HTTPException(status_code=401, detail="invalid authorization header")
        return None
    return parts[1]


def _owner_from_clerk_jwt(token: str) -> OwnerIdentity | None:
    claims = _jwt_payload(token)
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        return None
    email = _claim_string(claims, "email", "primary_email_address", "email_address")
    display_name = _claim_string(claims, "name", "full_name", "username")
    return OwnerIdentity(subject=subject, email=email, display_name=display_name)


def _claim_string(claims: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _jwt_payload(token: str) -> dict[str, object]:
    parts = token.split(".")
    if len(parts) < 2:
        return {}
    payload = parts[1]
    payload += "=" * (-len(payload) % 4)
    try:
        raw = base64.urlsafe_b64decode(payload.encode("ascii"))
        decoded = json.loads(raw)
    except (binascii.Error, json.JSONDecodeError, UnicodeDecodeError):
        return {}
    return decoded if isinstance(decoded, dict) else {}


def _is_trusted_lan_request(request: Request) -> bool:
    host = _request_host(request)
    if host is None:
        return False
    networks = []
    for raw in settings.trusted_cidrs.split(","):
        value = raw.strip()
        if not value:
            continue
        try:
            networks.append(ipaddress.ip_network(value, strict=False))
        except ValueError:
            continue
    return any(host in network for network in networks)


def _request_host(request: Request) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    forwarded_for = request.headers.get("x-forwarded-for", "").split(",", 1)[0].strip()
    raw_host = forwarded_for or (request.client.host if request.client else "")
    if raw_host == "testclient":
        raw_host = "127.0.0.1"
    try:
        return ipaddress.ip_address(raw_host)
    except ValueError:
        return None
