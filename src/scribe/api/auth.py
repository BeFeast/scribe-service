"""Request owner attribution and operator auth policy helpers."""

from __future__ import annotations

import base64
import binascii
import datetime as dt
import hashlib
import ipaddress
import json
import secrets
from dataclasses import dataclass
from enum import StrEnum

import httpx
import jwt
from fastapi import HTTPException, Request
from jwt import InvalidTokenError, PyJWK
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from scribe.config import settings

_JWKS_CACHE: dict[tuple[str, str], dict] = {}


class AuthState(StrEnum):
    public = "public"
    trusted_lan = "trusted_lan"
    machine_bearer = "machine_bearer"
    clerk_user = "clerk_user"
    extension_token = "extension_token"


@dataclass(frozen=True)
class OwnerIdentity:
    subject: str
    email: str | None = None
    display_name: str | None = None
    owner_id: int | None = None


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
    header_owner = _owner_from_clerk_headers(request)
    if header_owner is not None:
        return _authorized_clerk_owner(header_owner)

    bearer = _bearer_token(request)
    if bearer:
        machine_token = settings.machine_bearer_token.strip()
        if machine_token and secrets.compare_digest(bearer, machine_token):
            return default_owner()
        extension_owner = _owner_from_extension_token(bearer)
        if extension_owner is not None:
            return extension_owner
        clerk_owner = _owner_from_clerk_jwt(bearer)
        if clerk_owner is not None:
            if _is_trusted_lan_request(request):
                return clerk_owner
            return _authorized_clerk_owner(clerk_owner)

    if _is_trusted_lan_request(request):
        return default_owner()
    return None


def classify_auth(request: Request) -> AuthState:
    header_owner = _owner_from_clerk_headers(request)
    if header_owner is not None:
        _authorized_clerk_owner(header_owner)
        return AuthState.clerk_user

    bearer = _bearer_token(request, strict=True)
    if bearer:
        machine_token = settings.machine_bearer_token.strip()
        if machine_token and secrets.compare_digest(bearer, machine_token):
            return AuthState.machine_bearer
        if _owner_from_extension_token(bearer) is not None:
            return AuthState.extension_token
        if _clerk_configured():
            _validate_clerk_user(bearer)
            return AuthState.clerk_user
    if _is_trusted_lan_request(request):
        return AuthState.trusted_lan
    return AuthState.public


def require_operator_auth(request: Request) -> AuthState:
    state = classify_auth(request)
    if state == AuthState.public:
        raise HTTPException(status_code=401, detail="trusted LAN or bearer token required")
    return state


def is_trusted_lan_request(request: Request) -> bool:
    return _is_trusted_lan_request(request)


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


def _owner_from_clerk_headers(request: Request) -> OwnerIdentity | None:
    secret = settings.clerk_header_secret.strip()
    if not secret:
        return None
    if not secrets.compare_digest(request.headers.get("x-scribe-clerk-secret", ""), secret):
        return None
    subject = request.headers.get("x-clerk-user-id", "").strip()
    email = request.headers.get("x-clerk-user-email", "").strip()
    display_name = request.headers.get("x-clerk-user-name", "").strip() or None
    if not subject or not email:
        return None
    return OwnerIdentity(subject=subject, email=email, display_name=display_name)


def _owner_subject(owner_id: int) -> str:
    return f"owner:{owner_id}"


def _normalize_email(email: str) -> str:
    return email.strip().lower()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _owner_from_user(user) -> OwnerIdentity:
    return OwnerIdentity(
        subject=_owner_subject(user.owner_id),
        email=user.primary_email,
        display_name=user.display_name,
        owner_id=user.owner_id,
    )


def _create_local_user(session, owner: OwnerIdentity, *, role):
    from scribe.db.models import Owner, User

    display_name = owner.display_name or owner.email or owner.subject
    db_owner = Owner(display_name=display_name)
    session.add(db_owner)
    session.flush()
    user = User(
        owner_id=db_owner.id,
        clerk_subject=owner.subject,
        primary_email=_normalize_email(owner.email or ""),
        display_name=owner.display_name,
        role=role,
        is_active=True,
    )
    session.add(user)
    session.flush()
    return user


def _authorized_clerk_owner(owner: OwnerIdentity) -> OwnerIdentity:
    if owner.email is None:
        raise HTTPException(status_code=403, detail="Clerk user has no email")

    from scribe.db.models import User, UserRole
    from scribe.db.session import SessionLocal

    email = _normalize_email(owner.email)
    with SessionLocal() as session:
        user = session.scalar(select(User).where(User.clerk_subject == owner.subject))
        if user is None:
            user = session.scalar(select(User).where(User.primary_email == email))
            if user is not None and user.clerk_subject is None:
                user.clerk_subject = owner.subject
                if owner.display_name and not user.display_name:
                    user.display_name = owner.display_name
                session.commit()
        if user is None and int(session.scalar(select(func.count()).select_from(User)) or 0) == 0:
            bootstrap = _normalize_email(settings.auth_bootstrap_admin_email)
            if bootstrap and email == bootstrap:
                try:
                    user = _create_local_user(session, owner, role=UserRole.admin)
                    session.commit()
                except IntegrityError:
                    session.rollback()
                    user = session.scalar(select(User).where(User.clerk_subject == owner.subject))
                    if user is None:
                        user = session.scalar(select(User).where(User.primary_email == email))
        if user is None:
            raise HTTPException(status_code=403, detail="signed-in Clerk user is not authorized in Scribe")
        if not user.is_active:
            raise HTTPException(status_code=403, detail="Scribe user is disabled")
        return _owner_from_user(user)


def _owner_from_extension_token(token: str) -> OwnerIdentity | None:
    if not token.startswith("scribe_ext_"):
        return None

    from scribe.db.models import ExtensionToken
    from scribe.db.session import SessionLocal

    with SessionLocal() as session:
        row = session.scalar(select(ExtensionToken).where(ExtensionToken.token_hash == _hash_token(token)))
        if row is None or not row.is_active or not row.user.is_active:
            return None
        row.last_used_at = dt.datetime.now(dt.UTC)
        owner = _owner_from_user(row.user)
        session.commit()
        return owner


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


def _clerk_configured() -> bool:
    return bool(settings.auth_clerk_issuer.strip()) and (
        bool(settings.auth_clerk_jwks_url.strip()) or bool(settings.auth_clerk_jwks_json.strip())
    )


def _load_jwks() -> dict:
    inline = settings.auth_clerk_jwks_json.strip()
    if inline:
        cache_key = ("inline", inline)
        if cache_key not in _JWKS_CACHE:
            try:
                _JWKS_CACHE[cache_key] = json.loads(inline)
            except json.JSONDecodeError as exc:
                raise HTTPException(status_code=503, detail="Clerk JWKS JSON is invalid") from exc
        return _JWKS_CACHE[cache_key]

    url = settings.auth_clerk_jwks_url.strip()
    if not url:
        raise HTTPException(status_code=503, detail="Clerk JWKS is not configured")
    cache_key = ("url", url)
    if cache_key not in _JWKS_CACHE:
        try:
            response = httpx.get(url, timeout=5.0)
            response.raise_for_status()
            _JWKS_CACHE[cache_key] = response.json()
        except (httpx.HTTPError, json.JSONDecodeError) as exc:
            raise HTTPException(status_code=503, detail="Clerk JWKS fetch failed") from exc
    return _JWKS_CACHE[cache_key]


def _jwk_for_token(token: str) -> PyJWK:
    try:
        header = jwt.get_unverified_header(token)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid Clerk JWT") from exc

    kid = header.get("kid")
    keys = _load_jwks().get("keys", [])
    if not isinstance(keys, list):
        raise HTTPException(status_code=503, detail="Clerk JWKS is invalid")

    key = next((item for item in keys if isinstance(item, dict) and item.get("kid") == kid), None)
    if key is None and kid is None and len(keys) == 1 and isinstance(keys[0], dict):
        key = keys[0]
    if key is None:
        raise HTTPException(status_code=401, detail="invalid Clerk JWT")

    try:
        return PyJWK.from_dict(key)
    except InvalidTokenError as exc:
        raise HTTPException(status_code=503, detail="Clerk JWKS is invalid") from exc


def _validate_clerk_user(token: str) -> OwnerIdentity:
    issuer = settings.auth_clerk_issuer.strip()
    if not issuer:
        raise HTTPException(status_code=503, detail="Clerk issuer is not configured")

    jwk = _jwk_for_token(token)
    try:
        claims = jwt.decode(
            token,
            key=jwk.key,
            algorithms=["RS256"],
            issuer=issuer,
            options={"require": ["exp"], "verify_aud": False},
        )
    except InvalidTokenError as exc:
        raise HTTPException(status_code=401, detail="invalid Clerk JWT") from exc

    subject = str(claims.get("sub") or "").strip()
    if not subject:
        raise HTTPException(status_code=401, detail="invalid Clerk JWT")
    email = _claim_string(claims, "email", "primary_email_address", "email_address")
    display_name = _claim_string(claims, "name", "full_name", "username")
    return _authorized_clerk_owner(OwnerIdentity(subject=subject, email=email, display_name=display_name))


def clear_jwks_cache() -> None:
    _JWKS_CACHE.clear()
