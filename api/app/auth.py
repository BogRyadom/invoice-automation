import hmac
from collections.abc import Callable
from dataclasses import dataclass
from typing import Annotated, Any

import jwt
from fastapi import Header, HTTPException, Request, status

from app.config import Settings

AUDIENCE = "authenticated"
ALGORITHMS = ["ES256", "RS256"]


@dataclass(frozen=True)
class Reviewer:
    user_id: str
    email: str | None

    @property
    def name(self) -> str:
        """How the reviewer appears in events and on approved invoices."""
        return self.email or self.user_id


class TokenVerifier:
    def __init__(self, signing_key: Callable[[str], Any], issuer: str) -> None:
        self.signing_key = signing_key
        self.issuer = issuer

    @classmethod
    def for_supabase(cls, settings: Settings) -> "TokenVerifier":
        """Verify Supabase Auth tokens with the project's published signing keys."""
        jwks = jwt.PyJWKClient(
            f"{settings.supabase_url.rstrip('/')}/auth/v1/.well-known/jwks.json",
            cache_keys=True,
            lifespan=600,
        )
        issuer = f"{settings.supabase_public_url.rstrip('/')}/auth/v1"
        return cls(lambda token: jwks.get_signing_key_from_jwt(token).key, issuer)

    def verify(self, token: str) -> Reviewer:
        """Decode a bearer token or raise jwt.PyJWTError."""
        claims = jwt.decode(
            token,
            self.signing_key(token),
            algorithms=ALGORITHMS,
            audience=AUDIENCE,
            issuer=self.issuer,
            options={"require": ["exp", "sub"]},
        )
        return Reviewer(user_id=claims["sub"], email=claims.get("email"))


def unauthorized(detail: str) -> HTTPException:
    return HTTPException(
        status.HTTP_401_UNAUTHORIZED, detail=detail, headers={"WWW-Authenticate": "Bearer"}
    )


def require_reviewer(
    request: Request, authorization: Annotated[str | None, Header()] = None
) -> Reviewer:
    """Signed-in reviewer from the Supabase Auth bearer token."""
    if not authorization or not authorization.lower().startswith("bearer "):
        raise unauthorized("missing bearer token")
    verifier: TokenVerifier = request.app.state.token_verifier
    try:
        return verifier.verify(authorization.split(" ", 1)[1].strip())
    except (jwt.PyJWTError, jwt.PyJWKClientError) as exc:
        raise unauthorized("invalid token") from exc


def require_ingest_secret(
    request: Request, x_ingest_secret: Annotated[str | None, Header()] = None
) -> None:
    """Shared secret sent by n8n, compared in constant time."""
    settings: Settings = request.app.state.settings
    expected = settings.ingest_shared_secret.get_secret_value()
    if not expected:
        raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, "INGEST_SHARED_SECRET is not set")
    if not x_ingest_secret or not hmac.compare_digest(x_ingest_secret.encode(), expected.encode()):
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, "invalid ingest secret")
