"""
RNF-1 · Disponibilidad / RNF-2 · Integración (identidad) — guard dual de autenticación.

Prueba las dos vías de `require_user` (secreto interno del BFF y JWT de Keycloak)
y los helpers de roles. `_decode` se parchea; `ensure_user` se parchea a no-op para
no tocar Postgres.
"""
from __future__ import annotations

import pytest
from fastapi import HTTPException
from fastapi.security import HTTPAuthorizationCredentials

from infrastructure.auth import keycloak_guard as kg
from infrastructure.db import repository
from shared import config


def test_extract_roles_from_realm_access():
    claims = {"realm_access": {"roles": ["admin", "analista"]}}
    assert kg._extract_roles(claims) == ["admin", "analista"]


def test_extract_roles_empty_when_absent():
    assert kg._extract_roles({"other": 1}) == []


class _FakeKey:
    key = "fake-signing-key"


class _FakeClient:
    def get_signing_key_from_jwt(self, token):
        return _FakeKey()


def test_decode_valid_token(monkeypatch):
    monkeypatch.setattr(kg, "_jwk_client", lambda: _FakeClient())
    monkeypatch.setattr(kg.jwt, "decode",
                        lambda token, key, algorithms, issuer, options: {"sub": "u1", "email": "e@x.co"})
    claims = kg._decode("token")
    assert claims["sub"] == "u1"


def test_decode_invalid_token_raises_401(monkeypatch):
    monkeypatch.setattr(kg, "_jwk_client", lambda: _FakeClient())
    monkeypatch.setattr(kg.jwt, "decode",
                        lambda *a, **k: (_ for _ in ()).throw(Exception("bad signature")))
    with pytest.raises(HTTPException) as exc:
        kg._decode("token")
    assert exc.value.status_code == 401


# ── require_user: vía secreto interno (BFF) ───────────────────────────────────

def test_require_user_accepts_internal_secret(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "secret")
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    user = kg.require_user(x_internal_secret="secret", x_user_sub="u1",
                           x_user_roles="admin,analista",
                           creds=None, db=object())
    assert user.sub == "u1"
    assert user.roles == ["admin", "analista"]


def test_require_user_rejects_missing_sub(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "secret")
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    with pytest.raises(HTTPException) as exc:
        kg.require_user(x_internal_secret="secret", x_user_sub=None,
                        x_user_roles="", creds=None, db=object())
    assert exc.value.status_code == 401


def test_require_user_rejects_when_no_auth(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "secret")
    with pytest.raises(HTTPException) as exc:
        kg.require_user(x_internal_secret=None, x_user_sub=None,
                        x_user_roles=None, creds=None, db=object())
    assert exc.value.status_code == 401


# ── require_user: vía JWT de Keycloak ─────────────────────────────────────────

def test_require_user_accepts_jwt(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "")
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(kg, "_decode",
                        lambda token: {"sub": "u1", "email": "e@x.co",
                                       "realm_access": {"roles": ["admin"]}})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    user = kg.require_user(x_internal_secret=None, x_user_sub=None,
                           x_user_roles=None, creds=creds, db=object())
    assert user.sub == "u1"
    assert user.has_role("admin") is True


# ── require_role ──────────────────────────────────────────────────────────────

def test_require_role_allows_when_present(monkeypatch):
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(kg, "_decode",
                        lambda token: {"sub": "u1", "realm_access": {"roles": ["admin"]}})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    guard = kg.require_role("admin")
    user = guard(user=kg.require_user(creds=creds, db=object()))
    assert user.has_role("admin") is True


def test_require_role_rejects_when_missing(monkeypatch):
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(kg, "_decode",
                        lambda token: {"sub": "u1", "realm_access": {"roles": ["analista"]}})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    guard = kg.require_role("admin")
    with pytest.raises(HTTPException) as exc:
        guard(user=kg.require_user(creds=creds, db=object()))
    assert exc.value.status_code == 403


# ── require_auth (vía JWT directa, con Bearer obligatorio) ────────────────────

def test_require_auth_valid_token(monkeypatch):
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(kg, "_decode",
                        lambda token: {"sub": "u1", "email": "e@x.co",
                                       "realm_access": {"roles": ["admin"]}})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    user = kg.require_auth(creds=creds, db=object())
    assert user.sub == "u1"
    assert user.email == "e@x.co"


def test_require_auth_rejects_token_without_sub(monkeypatch):
    monkeypatch.setattr(kg, "_decode", lambda token: {"email": "e@x.co"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with pytest.raises(HTTPException) as exc:
        kg.require_auth(creds=creds, db=object())
    assert exc.value.status_code == 401


# ── require_user: JWT sin `sub` → 401 ─────────────────────────────────────────

def test_require_user_rejects_jwt_without_sub(monkeypatch):
    monkeypatch.setattr(config, "INTERNAL_API_SECRET", "")
    monkeypatch.setattr(repository, "ensure_user", lambda *a, **k: None)
    monkeypatch.setattr(kg, "_decode", lambda token: {"email": "e@x.co"})
    creds = HTTPAuthorizationCredentials(scheme="Bearer", credentials="token")
    with pytest.raises(HTTPException) as exc:
        kg.require_user(x_internal_secret=None, x_user_sub=None,
                        x_user_roles=None, creds=creds, db=object())
    assert exc.value.status_code == 401


def test_jwk_client_is_cached(monkeypatch):
    # Construir el cliente no requiere red; solo se guarda la URL.
    monkeypatch.setattr(config, "KEYCLOAK_JWKS_URL", "http://localhost/certs")
    kg._jwk_client.cache_clear()
    assert kg._jwk_client() is kg._jwk_client()  # lru_cache devuelve el mismo objeto
