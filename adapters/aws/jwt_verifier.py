# adapters/aws/jwt_verifier.py
"""Cognito JWT token verification using JWKS (RS256)."""

import time
import structlog
import httpx
from jose import jwt, JWTError

logger = structlog.get_logger(__name__)

# Cache JWKS keys for 1 hour to avoid fetching on every request
_jwks_cache: dict[str, list[dict]] = {}
_jwks_cache_time: float = 0.0
JWKS_CACHE_TTL = 3600  # 1 hour


class CognitoJWTVerifier:
    """Verifies Cognito JWT tokens using the User Pool's public JWKS."""

    def __init__(self, region: str, user_pool_id: str, app_client_id: str):
        self.region = region
        self.user_pool_id = user_pool_id
        self.app_client_id = app_client_id
        self.issuer = f"https://cognito-idp.{region}.amazonaws.com/{user_pool_id}"
        self.jwks_url = f"{self.issuer}/.well-known/jwks.json"
        self._keys: list[dict] = []
        logger.info("jwt_verifier_initialized", issuer=self.issuer)

    def _fetch_jwks(self) -> list[dict]:
        """Fetch and cache the JSON Web Key Set from Cognito."""
        global _jwks_cache, _jwks_cache_time

        now = time.time()
        cache_key = self.jwks_url

        if cache_key in _jwks_cache and (now - _jwks_cache_time) < JWKS_CACHE_TTL:
            return _jwks_cache[cache_key]

        try:
            response = httpx.get(self.jwks_url, timeout=5.0)
            response.raise_for_status()
            keys = response.json().get("keys", [])
            _jwks_cache[cache_key] = keys
            _jwks_cache_time = now
            logger.info("jwks_fetched", key_count=len(keys))
            return keys
        except Exception as e:
            logger.error("jwks_fetch_failed", error=str(e))
            # Return cached keys if available, even if stale
            if cache_key in _jwks_cache:
                return _jwks_cache[cache_key]
            raise

    def _get_signing_key(self, token: str) -> dict:
        """Find the correct signing key for the token's kid header."""
        keys = self._fetch_jwks()
        unverified_header = jwt.get_unverified_header(token)
        kid = unverified_header.get("kid")

        for key in keys:
            if key.get("kid") == kid:
                return key

        raise JWTError(f"Unable to find matching key for kid: {kid}")

    def verify_token(self, token: str, token_use: str = "access") -> dict:
        """Verify a Cognito JWT token and return its claims.

        Args:
            token: The raw JWT string (without 'Bearer ' prefix).
            token_use: Expected token type — 'access' or 'id'.

        Returns:
            dict of verified claims.

        Raises:
            JWTError: If the token is invalid, expired, or tampered with.
        """
        try:
            signing_key = self._get_signing_key(token)

            claims = jwt.decode(
                token,
                signing_key,
                algorithms=["RS256"],
                audience=self.app_client_id if token_use == "id" else None,
                issuer=self.issuer,
                options={
                    "verify_aud": token_use == "id",
                    "verify_exp": True,
                    "verify_iss": True,
                },
            )

            # Verify token_use claim
            actual_use = claims.get("token_use", "")
            if actual_use != token_use:
                raise JWTError(f"Token use mismatch: expected '{token_use}', got '{actual_use}'")

            # For access tokens, verify client_id claim
            if token_use == "access":
                client_id = claims.get("client_id", "")
                if client_id != self.app_client_id:
                    raise JWTError(
                        f"Client ID mismatch: expected '{self.app_client_id}', got '{client_id}'"
                    )

            logger.info(
                "jwt_verified",
                sub=claims.get("sub", ""),
                token_use=token_use,
            )
            return claims

        except JWTError as e:
            logger.warning("jwt_verification_failed", error=str(e))
            raise
        except Exception as e:
            logger.error("jwt_verification_error", error=str(e))
            raise JWTError(f"Token verification failed: {str(e)}")
