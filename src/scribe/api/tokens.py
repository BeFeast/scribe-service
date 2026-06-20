"""Machine bearer token rotation with a previous-generation grace window.

The machine bearer token historically lived only in ``SCRIBE_MACHINE_BEARER_TOKEN``
(env), so rotating a leaked token required a redeploy. This module stores
SHA-256 hashes of the active and previous-generation tokens in ``app_config``
so ``POST /api/config/rotate-token`` can rotate without a restart:

* ``machine_bearer_token_hash`` — hash of the current active token.
* ``machine_bearer_token_prev_hash`` — hash of the token the rotation
  demoted (the env token on first rotation, or the prior current hash).
* ``machine_bearer_token_prev_rotated_at`` — ISO-8601 UTC timestamp marking
  the start of the grace window for the previous generation.

When no rotation has ever been recorded (cold boot, empty ``app_config``), the
env ``machine_bearer_token`` remains authoritative for backward compatibility.
After the first rotation the env token is demoted into the previous
generation and is only accepted within ``machine_bearer_grace_seconds``.

The hashed state is cached in-process for a short TTL to avoid a DB round-trip
on every authenticated request. The rotate endpoint busts the cache so a
rotation is visible immediately; the grace-window expiry is evaluated against
the wall clock at match time, so a cached state cannot extend acceptance past
the grace window.
"""
from __future__ import annotations

import datetime as dt
import hashlib
import secrets
import threading
import time
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from scribe.config import settings
from scribe.db.models import AppConfig

MACHINE_BEARER_TOKEN_HASH_KEY = "machine_bearer_token_hash"
MACHINE_BEARER_TOKEN_PREV_HASH_KEY = "machine_bearer_token_prev_hash"
MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY = "machine_bearer_token_prev_rotated_at"

_MACHINE_BEARER_PREFIX = "stb_"
_CACHE_TTL_SECONDS = 5.0

_state_lock = threading.Lock()
_state_cache: tuple[MachineBearerState, float] | None = None


@dataclass(frozen=True)
class MachineBearerState:
    """Snapshot of the rotated-token rows. ``current_hash`` is ``None`` when no
    rotation has ever been recorded, in which case the env token is
    authoritative."""

    current_hash: str | None
    prev_hash: str | None
    prev_rotated_at: dt.datetime | None


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_machine_bearer_token() -> str:
    """Generate a fresh plaintext machine bearer token (returned once to the
    operator; only its hash is persisted)."""
    return f"{_MACHINE_BEARER_PREFIX}{secrets.token_urlsafe(32)}"


def _read_state(session: Session) -> MachineBearerState:
    rows = dict(
        session.execute(
            select(AppConfig.key, AppConfig.value).where(
                AppConfig.key.in_(
                    [
                        MACHINE_BEARER_TOKEN_HASH_KEY,
                        MACHINE_BEARER_TOKEN_PREV_HASH_KEY,
                        MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY,
                    ]
                )
            )
        ).all()
    )
    current_hash = (rows.get(MACHINE_BEARER_TOKEN_HASH_KEY) or "").strip() or None
    prev_hash = (rows.get(MACHINE_BEARER_TOKEN_PREV_HASH_KEY) or "").strip() or None
    rotated_raw = (rows.get(MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY) or "").strip()
    prev_rotated_at: dt.datetime | None = None
    if rotated_raw:
        try:
            prev_rotated_at = dt.datetime.fromisoformat(rotated_raw)
        except ValueError:
            prev_rotated_at = None
    return MachineBearerState(
        current_hash=current_hash,
        prev_hash=prev_hash,
        prev_rotated_at=prev_rotated_at,
    )


def _load_state(session: Session | None) -> MachineBearerState:
    """Return the current rotated-token state, using a short in-process cache.

    On DB failure we prefer the last observed state (preserves rotation
    security rather than silently falling back to the env token) and only
    degrade to env-only at cold boot when no rotation has ever been seen. The
    cold-boot fallback is also cached briefly so a DB outage does not turn
    every request into a connection attempt.
    """
    global _state_cache
    now = time.monotonic()
    with _state_lock:
        cached = _state_cache
        if cached is not None and now - cached[1] < _CACHE_TTL_SECONDS:
            return cached[0]

    try:
        if session is not None:
            state = _read_state(session)
        else:
            # Imported lazily so non-DB test contexts can import this module
            # without constructing a engine just to read token state.
            from scribe.db.session import SessionLocal

            with SessionLocal() as s:
                state = _read_state(s)
    except Exception:
        if cached is not None:
            return cached[0]
        state = MachineBearerState(current_hash=None, prev_hash=None, prev_rotated_at=None)

    with _state_lock:
        _state_cache = (state, now)
    return state


def bust_machine_bearer_cache() -> None:
    """Drop the in-process state cache. Called after a rotation so the next
    auth request observes the new hashes immediately."""
    global _state_cache
    with _state_lock:
        _state_cache = None


def machine_bearer_matches(bearer: str, session: Session | None = None) -> bool:
    """Return True if ``bearer`` is the active machine bearer token, or the
    previous generation still inside its grace window."""
    if not bearer:
        return False
    state = _load_state(session)
    if state.current_hash is None:
        # No rotation has ever been recorded: env token is authoritative.
        env_token = settings.machine_bearer_token.strip()
        return bool(env_token) and secrets.compare_digest(bearer, env_token)

    bearer_hash = _hash_token(bearer)
    if secrets.compare_digest(bearer_hash, state.current_hash):
        return True
    if state.prev_hash and secrets.compare_digest(bearer_hash, state.prev_hash):
        if state.prev_rotated_at is not None:
            age = (dt.datetime.now(dt.UTC) - state.prev_rotated_at).total_seconds()
            if 0 <= age < settings.machine_bearer_grace_seconds:
                return True
    return False


def _upsert(session: Session, key: str, value: str) -> None:
    row = session.get(AppConfig, key)
    if row is None:
        session.add(AppConfig(key=key, value=value))
    else:
        row.value = value


def rotate_machine_bearer_token(session: Session) -> str:
    """Rotate the machine bearer token and return the new plaintext token once.

    The currently active token (the env token on first rotation, or the prior
    ``current_hash`` on subsequent rotations) is demoted into the previous
    generation with a fresh grace window. The new token's hash becomes the
    active ``current_hash``. The in-process cache is busted so the next auth
    request sees the rotation immediately.
    """
    state = _read_state(session)
    if state.current_hash:
        prev_hash = state.current_hash
    else:
        env_token = settings.machine_bearer_token.strip()
        prev_hash = _hash_token(env_token) if env_token else ""

    new_token = new_machine_bearer_token()
    now = dt.datetime.now(dt.UTC)
    _upsert(session, MACHINE_BEARER_TOKEN_HASH_KEY, _hash_token(new_token))
    _upsert(session, MACHINE_BEARER_TOKEN_PREV_HASH_KEY, prev_hash)
    _upsert(session, MACHINE_BEARER_TOKEN_PREV_ROTATED_AT_KEY, now.isoformat())
    session.commit()
    bust_machine_bearer_cache()
    return new_token
