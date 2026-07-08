"""Request authentication, owner attribution, and authorization helpers."""
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
from typing import Literal
from urllib.parse import quote

import httpx
import jwt
from fastapi import HTTPException, Request
from fastapi.security import HTTPAuthorizationCredentials
from jwt import InvalidTokenError, PyJWK
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from scribe.api.tokens import machine_bearer_matches
from scribe.config import settings
from scribe.db.models import ExtensionToken, Owner, User

_JWKS_CACHE: dict[tuple[str, str], dict] = {}
_CLERK_USER_CACHE: dict[str, OwnerIdentity | None] = {}
_TOKEN_PREFIX = "stx_"

Role = Literal["admin", "user", "machine", "lan"]


class AuthState(StrEnum):
    public = "public"
    trusted_lan = "trusted_lan"
    machine_bearer = "machine_bearer"
    clerk_user = "clerk_user"


@dataclass(frozen=True)
class OwnerIdentity:
    subject: str
    email: str | None = None
    display_name: str | None = None


@dataclass(frozen=True)
class Actor:
    kind: str
    role: Role
    subject: str | None = None
    user_id: int | None = None
    owner_id: int | None = None
    email: str | None = None
    display_name: str | None = None

    @property
    def is_admin(self) -> bool:
        return self.role in {"admin", "machine", "lan"}

    @property
    def is_trusted_lan(self) -> bool:
        """A plain trusted-LAN actor: authenticated by network, no owner and
        not a machine-bearer. Distinguishes the LAN operator (kind
        ``trusted-lan``) from a machine token (kind ``machine``), which is a
        shared infra credential — both have ``owner_id is None`` (#405)."""
        return self.kind == "trusted-lan"


def token_hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_extension_token() -> str:
    return f"{_TOKEN_PREFIX}{secrets.token_urlsafe(32)}"


def default_owner() -> OwnerIdentity | None:
    subject = settings.default_owner_subject.strip() or settings.default_owner_email.strip()
    if not subject:
        return None
    return OwnerIdentity(
        subject=subject,
        email=settings.default_owner_email.strip() or None,
        display_name=None,
    )


def current_owner(request: Request, session: Session | None = None) -> OwnerIdentity | None:
    bearer = _bearer_token(request)
    if bearer:
        if machine_bearer_matches(bearer, session):
            return default_owner()
        clerk_owner = _owner_from_clerk_jwt(bearer, verify=False)
        if clerk_owner is not None:
            return clerk_owner

    if is_trusted_lan_request(request):
        return default_owner()
    return None


def classify_auth(request: Request, session: Session | None = None) -> AuthState:
    bearer = _bearer_token(request, strict=True)
    if bearer:
        if machine_bearer_matches(bearer, session):
            return AuthState.machine_bearer
        if _clerk_configured():
            _validate_clerk_user(bearer)
            return AuthState.clerk_user
    if is_trusted_lan_request(request):
        return AuthState.trusted_lan
    return AuthState.public


def require_operator_auth(request: Request) -> AuthState:
    state = classify_auth(request)
    if state == AuthState.public:
        raise HTTPException(status_code=401, detail="trusted LAN or bearer token required")
    return state


def is_trusted_lan_request(request: Request) -> bool:
    return _is_trusted_host(_client_ip(request))


def lan_request_proxy_safe(request: Request) -> bool:
    """Is the trusted-LAN classification safe from reverse-proxy laundering?

    ``is_trusted_lan_request`` trusts the immediate peer, so a reverse proxy on
    a trusted address (e.g. loopback, which is in the default ``trusted_cidrs``)
    makes every forwarded external client look like the LAN. If a request
    carries an ``X-Forwarded-For`` header but no ``trusted_proxies`` are
    configured, the proxy is undeclared: the real client is unknown and the
    header is client-spoofable (see ``_client_ip``), so the classification must
    not be used to grant a privilege like the #405 cookie exception. Returns
    ``False`` in that case, ``True`` otherwise (direct connection, or a proxy
    whose topology the operator has declared via ``trusted_proxies``)."""
    if request.headers.get("x-forwarded-for") and not settings.trusted_proxies.strip():
        return False
    return True


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


def _trusted_networks() -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for raw in settings.trusted_cidrs.split(","):
        value = raw.strip()
        if not value:
            continue
        networks.append(ipaddress.ip_network(value, strict=False))
    return networks


def _trusted_proxy_networks() -> list[ipaddress._BaseNetwork]:
    networks: list[ipaddress._BaseNetwork] = []
    for raw in settings.trusted_proxies.split(","):
        value = raw.strip()
        if not value:
            continue
        networks.append(ipaddress.ip_network(value, strict=False))
    return networks


def _is_trusted_host(host: str) -> bool:
    if host == "testclient":
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in _trusted_networks())


def _host_in_networks(host: str, networks: list[ipaddress._BaseNetwork]) -> bool:
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return False
    return any(ip in network for network in networks)


def _client_ip(request: Request) -> str:
    """Resolve the real client IP for CIDR auth + logging (#348).

    With ``trusted_proxies`` configured, X-Forwarded-For is honoured only when
    the immediate peer is a configured proxy, and the chain is walked
    right-to-left skipping trusted proxies so a client cannot spoof the
    leftmost entry. Without ``trusted_proxies`` the safe default is preserved
    (XFF is ignored unless the immediate peer is itself trusted via
    ``trusted_cidrs`` / testclient), matching pre-#348 behaviour.
    """
    peer = request.client.host if request.client else ""
    xff = request.headers.get("x-forwarded-for", "")
    proxies = _trusted_proxy_networks()
    if proxies:
        if not _host_in_networks(peer, proxies):
            return peer
        candidates = [c.strip() for c in xff.split(",") if c.strip()]
        for candidate in reversed(candidates):
            if not _host_in_networks(candidate, proxies):
                return candidate
        return peer
    forwarded_for = xff.split(",", 1)[0].strip()
    if forwarded_for and _is_trusted_host(peer):
        return forwarded_for
    return peer


def _normal_email(value: str | None) -> str | None:
    if value is None:
        return None
    normalized = value.strip().lower()
    return normalized or None


def _claim_string(claims: dict[str, object], *keys: str) -> str | None:
    for key in keys:
        value = claims.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def _claims_email(claims: dict[str, object]) -> str | None:
    return _normal_email(
        _claim_string(claims, "email", "primary_email", "primary_email_address", "email_address")
    )


def _claims_name(claims: dict[str, object]) -> str | None:
    value = _claim_string(claims, "name", "full_name", "display_name", "username")
    if value:
        return value
    first = claims.get("given_name")
    last = claims.get("family_name")
    joined = " ".join(part.strip() for part in (first, last) if isinstance(part, str) and part.strip())
    return joined or None


def _clerk_profile_email(data: dict[str, object]) -> str | None:
    emails = data.get("email_addresses")
    if not isinstance(emails, list):
        return None

    primary_id = data.get("primary_email_address_id")
    if isinstance(primary_id, str) and primary_id:
        for item in emails:
            if isinstance(item, dict) and item.get("id") == primary_id:
                value = item.get("email_address")
                return _normal_email(value if isinstance(value, str) else None)

    for item in emails:
        if isinstance(item, dict):
            value = item.get("email_address")
            email = _normal_email(value if isinstance(value, str) else None)
            if email:
                return email
    return None


def _clerk_profile_name(data: dict[str, object]) -> str | None:
    name = _claim_string(data, "full_name", "name", "username")
    if name:
        return name
    first = data.get("first_name")
    last = data.get("last_name")
    joined = " ".join(part.strip() for part in (first, last) if isinstance(part, str) and part.strip())
    return joined or None


def _clerk_user_profile(subject: str) -> OwnerIdentity | None:
    if subject in _CLERK_USER_CACHE:
        return _CLERK_USER_CACHE[subject]

    secret = settings.clerk_secret_key.strip()
    if not secret:
        _CLERK_USER_CACHE[subject] = None
        return None

    base_url = settings.clerk_backend_api_url.strip().rstrip("/") or "https://api.clerk.com"
    try:
        response = httpx.get(
            f"{base_url}/v1/users/{quote(subject, safe='')}",
            headers={"Authorization": f"Bearer {secret}"},
            timeout=5.0,
        )
        response.raise_for_status()
        data = response.json()
    except (httpx.HTTPError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=503, detail="Clerk user lookup failed") from exc

    profile = (
        OwnerIdentity(
            subject=subject,
            email=_clerk_profile_email(data),
            display_name=_clerk_profile_name(data),
        )
        if isinstance(data, dict)
        else None
    )
    _CLERK_USER_CACHE[subject] = profile
    return profile


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


def _owner_from_clerk_jwt(token: str, *, verify: bool = True) -> OwnerIdentity | None:
    claims = _validate_clerk_user(token) if verify else _jwt_payload(token)
    subject = str(claims.get("sub") or "").strip()
    if not subject:
        return None
    return OwnerIdentity(subject=subject, email=_claims_email(claims), display_name=_claims_name(claims))


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


def _validate_clerk_user(token: str) -> dict[str, object]:
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
    if not isinstance(claims, dict):
        return {}
    allowed = _allowed_emails()
    if allowed:
        email = _claims_email(claims)
        if email is None or email not in allowed:
            raise HTTPException(status_code=403, detail="email is not allowed")
    return claims


def _allowed_emails() -> frozenset[str]:
    return frozenset(
        email.strip().lower() for email in settings.auth_allowed_emails.replace("\n", ",").split(",") if email.strip()
    )


def _actor_from_user(user: User, *, kind: str) -> Actor:
    if user.disabled:
        raise HTTPException(status_code=403, detail="Scribe user is disabled")
    if user.role not in {"admin", "user"}:
        raise HTTPException(status_code=403, detail="Scribe user role is invalid")
    return Actor(
        kind=kind,
        role=user.role,  # type: ignore[arg-type]
        subject=user.clerk_subject,
        user_id=user.id,
        owner_id=user.owner_id,
        email=user.primary_email,
        display_name=user.display_name,
    )


def _bootstrap_user(session: Session, *, subject: str, email: str, display_name: str | None) -> User | None:
    bootstrap_email = _normal_email(settings.bootstrap_admin_email)
    if bootstrap_email != email:
        return None
    user_count = session.scalar(select(func.count()).select_from(User)) or 0
    if user_count:
        return None
    owner = Owner(display_name=display_name or email)
    user = User(
        owner=owner,
        clerk_subject=subject,
        primary_email=email,
        display_name=display_name,
        role="admin",
    )
    session.add(user)
    session.commit()
    session.refresh(user)
    return user


def _find_or_link_user(
    session: Session, *, subject: str, email: str, display_name: str | None
) -> User | None:
    subject_user = session.scalar(select(User).where(User.clerk_subject == subject))
    email_user = session.scalar(select(User).where(User.primary_email == email))
    if subject_user is not None and email_user is not None and subject_user.id != email_user.id:
        raise HTTPException(status_code=403, detail="Clerk identity conflicts with an existing Scribe user")

    user = subject_user or email_user
    if user is None:
        return _bootstrap_user(session, subject=subject, email=email, display_name=display_name)
    if user.clerk_subject is None:
        user.clerk_subject = subject
    elif user.clerk_subject != subject:
        raise HTTPException(status_code=403, detail="Clerk identity conflicts with an existing Scribe user")
    if display_name and user.display_name != display_name:
        user.display_name = display_name
    if user.primary_email != email:
        user.primary_email = email
    session.commit()
    session.refresh(user)
    return user


def _actor_from_clerk_token(session: Session, token: str) -> Actor:
    claims = _validate_clerk_user(token)
    subject = str(claims.get("sub") or "").strip()
    email = _claims_email(claims)
    if not subject or email is None:
        if not subject:
            raise HTTPException(status_code=401, detail="Clerk token is missing subject")

    display_name = _claims_name(claims)
    if email is None or display_name is None:
        profile = _clerk_user_profile(subject)
        if profile is not None:
            email = email or profile.email
            display_name = display_name or profile.display_name

    subject_user = session.scalar(select(User).where(User.clerk_subject == subject))
    if subject_user is not None and email is None:
        if display_name and subject_user.display_name != display_name:
            subject_user.display_name = display_name
            session.commit()
            session.refresh(subject_user)
        return _actor_from_user(subject_user, kind="clerk")

    if email is None:
        raise HTTPException(status_code=401, detail="Clerk token is missing email")

    user = _find_or_link_user(session, subject=subject, email=email, display_name=display_name)
    if user is None:
        raise HTTPException(status_code=403, detail="Scribe access is not allowed for this Clerk user")
    return _actor_from_user(user, kind="clerk")


def _actor_from_extension_token(session: Session, token: str) -> Actor | None:
    if not token.startswith(_TOKEN_PREFIX):
        return None
    row = session.scalar(select(ExtensionToken).where(ExtensionToken.token_hash == token_hash(token)))
    if row is None or row.disabled:
        raise HTTPException(status_code=401, detail="invalid extension token")
    row.last_used_at = dt.datetime.now(dt.UTC)
    session.commit()
    return _actor_from_user(row.user, kind="extension")


def _actor_from_test_headers(request: Request, session: Session) -> Actor | None:
    if not settings.auth_test_mode:
        return None
    subject = request.headers.get("x-scribe-test-clerk-sub")
    email = _normal_email(request.headers.get("x-scribe-test-email"))
    if not subject or not email:
        return None
    user = _find_or_link_user(
        session,
        subject=subject,
        email=email,
        display_name=request.headers.get("x-scribe-test-name"),
    )
    if user is None:
        raise HTTPException(status_code=403, detail="Scribe access is not allowed for this Clerk user")
    return _actor_from_user(user, kind="clerk-test")


def current_actor(
    request: Request,
    session: Session,
    credentials: HTTPAuthorizationCredentials | None,
) -> Actor:
    if test_actor := _actor_from_test_headers(request, session):
        return test_actor
    if request.headers.get("authorization") and credentials is None:
        raise HTTPException(status_code=401, detail="invalid authorization header")
    if credentials is not None and credentials.scheme.lower() == "bearer":
        token = credentials.credentials
        if machine_bearer_matches(token, session):
            owner = default_owner()
            return Actor(
                kind="machine",
                role="machine",
                subject=owner.subject if owner else None,
                email=owner.email if owner else None,
                display_name=owner.display_name if owner else None,
            )
        if extension_actor := _actor_from_extension_token(session, token):
            return extension_actor
        if not _clerk_configured():
            if is_trusted_lan_request(request):
                owner = _owner_from_clerk_jwt(token, verify=False)
                if owner is not None:
                    return Actor(
                        kind="legacy-clerk",
                        role="user",
                        subject=owner.subject,
                        email=owner.email,
                        display_name=owner.display_name,
                    )
            raise HTTPException(status_code=401, detail="invalid bearer token")
        return _actor_from_clerk_token(session, token)
    if is_trusted_lan_request(request):
        return Actor(kind="trusted-lan", role="lan")
    raise HTTPException(status_code=401, detail="authentication required")


def clear_jwks_cache() -> None:
    _JWKS_CACHE.clear()
    _CLERK_USER_CACHE.clear()
