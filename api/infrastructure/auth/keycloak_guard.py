"""
JWT Guard — valida tokens OIDC emitidos por Keycloak.
Usa la clave pública del realm para verificar firma RS256.
"""
import httpx
import jwt
from jwt import PyJWKClient
from fastapi import HTTPException, Security
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from functools import lru_cache
from shared.config import KEYCLOAK_URL, KEYCLOAK_REALM

_bearer = HTTPBearer(auto_error=False)

ISSUER = f"{KEYCLOAK_URL}/realms/{KEYCLOAK_REALM}"
JWKS_URL = f"{ISSUER}/protocol/openid-connect/certs"
ALGORITHMS = ["RS256"]


@lru_cache(maxsize=1)
def _get_jwk_client() -> PyJWKClient:
    return PyJWKClient(JWKS_URL)


def _decode_token(token: str) -> dict:
    """Decodifica y valida un JWT de Keycloak."""
    try:
        signing_key = _get_jwk_client().get_signing_key_from_jwt(token)
        payload = jwt.decode(
            token,
            signing_key.key,
            algorithms=ALGORITHMS,
            issuer=ISSUER,
            options={"verify_aud": False},  # Keycloak no siempre incluye aud
        )
        return payload
    except jwt.ExpiredSignatureError:
        raise HTTPException(status_code=401, detail="Token expirado")
    except jwt.InvalidTokenError as e:
        raise HTTPException(status_code=401, detail=f"Token inválido: {e}")
    except Exception as e:
        raise HTTPException(status_code=401, detail=f"Error de autenticación: {e}")


async def require_auth(
    credentials: HTTPAuthorizationCredentials = Security(_bearer),
) -> dict:
    """Dependency de FastAPI — inyectar en rutas protegidas."""
    if not credentials:
        raise HTTPException(status_code=401, detail="Token requerido")
    return _decode_token(credentials.credentials)


async def require_role(role: str):
    """Factory para exigir un rol específico del realm."""
    async def _guard(
        credentials: HTTPAuthorizationCredentials = Security(_bearer),
    ) -> dict:
        if not credentials:
            raise HTTPException(status_code=401, detail="Token requerido")
        payload = _decode_token(credentials.credentials)
        realm_roles = payload.get("realm_access", {}).get("roles", [])
        if role not in realm_roles:
            raise HTTPException(status_code=403, detail=f"Rol '{role}' requerido")
        return payload
    return _guard
