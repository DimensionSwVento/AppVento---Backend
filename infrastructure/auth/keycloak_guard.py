"""
Guard de autenticación / autorización.

Flujo: el cliente manda `Authorization: Bearer <access_token>` (emitido por
Keycloak). Aquí se **verifica** el JWT (firma RS256 vía JWKS + issuer), se
**decodifica el nivel de permiso** (roles del realm), y se **verifica/provisiona
el usuario en Postgres**. El guard acepta o rechaza. NO hay login ni registro —
de eso se encarga Keycloak.
"""
from __future__ import annotations
from dataclasses import dataclass
from functools import lru_cache

import jwt
from jwt import PyJWKClient
from fastapi import Depends, Header, HTTPException, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlalchemy.orm import Session

from shared import config
from infrastructure.db import repository
from infrastructure.db.session import get_db

_bearer = HTTPBearer(auto_error=True)
_bearer_optional = HTTPBearer(auto_error=False)


@lru_cache(maxsize=1)
def _jwk_client() -> PyJWKClient:
    # Cachea las claves públicas del realm (JWKS) — evita ir a Keycloak por request.
    return PyJWKClient(config.KEYCLOAK_JWKS_URL)


@dataclass
class CurrentUser:
    sub: str
    email: str | None
    roles: list[str]

    def has_role(self, role: str) -> bool:
        return role in self.roles


def _decode(token: str) -> dict:
    try:
        signing_key = _jwk_client().get_signing_key_from_jwt(token).key
        return jwt.decode(
            token,
            signing_key,
            algorithms=config.JWT_ALGORITHMS,
            issuer=config.KEYCLOAK_ISSUER,
            # El `aud` de Keycloak varía por cliente; validamos firma + issuer.
            options={"verify_aud": False},
        )
    except Exception as exc:  # firma inválida, expirado, issuer erróneo…
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail=f"Token inválido: {exc}",
        ) from exc


def _extract_roles(claims: dict) -> list[str]:
    realm_access = claims.get("realm_access") or {}
    return list(realm_access.get("roles", []))


def require_auth(
    creds: HTTPAuthorizationCredentials = Depends(_bearer),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """Verifica el token, provisiona el usuario en BD y devuelve el usuario actual."""
    claims = _decode(creds.credentials)
    sub = claims.get("sub")
    if not sub:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Token sin 'sub'")

    email = claims.get("email")
    roles = _extract_roles(claims)

    # Verificación / provisión del usuario en Postgres (consulta a BD).
    repository.ensure_user(db, sub=sub, email=email, roles=roles)

    return CurrentUser(sub=sub, email=email, roles=roles)


def require_user(
    x_internal_secret: str | None = Header(default=None, alias="X-Internal-Secret"),
    x_user_sub: str | None = Header(default=None, alias="X-User-Sub"),
    x_user_roles: str | None = Header(default=None, alias="X-User-Roles"),
    creds: HTTPAuthorizationCredentials | None = Depends(_bearer_optional),
    db: Session = Depends(get_db),
) -> CurrentUser:
    """
    Guard dual — acepta dos vías de autenticación:
    1. **BFF interno** (Next → FastAPI en AccuWeb): `X-Internal-Secret` válido +
       `X-User-Sub`/`X-User-Roles` (identidad ya verificada por NextAuth).
    2. **JWT de Keycloak** directo (llamadas del browser).
    En ambos casos verifica/provisiona el usuario en Postgres.
    """
    # 1) Vía BFF interno.
    if config.INTERNAL_API_SECRET and x_internal_secret == config.INTERNAL_API_SECRET:
        if not x_user_sub:
            raise HTTPException(status_code=401, detail="Falta X-User-Sub")
        roles = [r for r in (x_user_roles or "").split(",") if r]
        repository.ensure_user(db, sub=x_user_sub, email=None, roles=roles)
        return CurrentUser(sub=x_user_sub, email=None, roles=roles)

    # 2) Vía JWT de Keycloak.
    if creds is not None:
        claims = _decode(creds.credentials)
        sub = claims.get("sub")
        if not sub:
            raise HTTPException(status_code=401, detail="Token sin 'sub'")
        email = claims.get("email")
        roles = _extract_roles(claims)
        repository.ensure_user(db, sub=sub, email=email, roles=roles)
        return CurrentUser(sub=sub, email=email, roles=roles)

    raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="No autenticado")


def require_role(role: str):
    """
    Factory de guard por rol: el guard decodifica el nivel de permiso y rechaza
    (403) si el usuario no tiene el rol. Uso: `Depends(require_role("admin"))`.
    """

    def _guard(user: CurrentUser = Depends(require_auth)) -> CurrentUser:
        if not user.has_role(role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"Requiere el rol '{role}'",
            )
        return user

    return _guard
