# coaction_agent_platform/app/routers/auth_router.py
"""Authentication endpoints using AWS Cognito."""

import structlog
from fastapi import APIRouter, HTTPException
from pydantic import BaseModel
from botocore.exceptions import ClientError

logger = structlog.get_logger(__name__)
router = APIRouter(prefix="/auth", tags=["Authentication"])

# Module-level reference to Cognito adapter — set at app startup
_cognito = None
_dynamodb = None


def init_auth_router(cognito_adapter, dynamodb_adapter) -> None:
    """Initialize auth router with adapter instances (called at app startup)."""
    global _cognito, _dynamodb
    _cognito = cognito_adapter
    _dynamodb = dynamodb_adapter


# ── Request / Response Models ────────────────────────────────────────────


class SignupRequest(BaseModel):
    email: str
    password: str
    name: str
    role: str = "agent"


class ConfirmRequest(BaseModel):
    email: str
    confirmation_code: str


class LoginRequest(BaseModel):
    email: str
    password: str


class LoginResponse(BaseModel):
    access_token: str
    id_token: str
    refresh_token: str
    user: dict


# ── Endpoints ────────────────────────────────────────────────────────────


@router.post("/signup")
async def signup(req: SignupRequest):
    """Register a new user in Cognito."""
    if not _cognito:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    valid_roles = {"agent", "underwriter", "external"}
    role = req.role.strip().lower()
    if role not in valid_roles:
        raise HTTPException(status_code=400, detail=f"Invalid role. Must be one of: {valid_roles}")

    try:
        result = _cognito.sign_up(
            email=req.email.strip(),
            password=req.password,
            name=req.name.strip(),
            role=role,
        )
        return {
            "message": "Signup successful. Please check your email for a verification code.",
            "user_sub": result["user_sub"],
            "confirmed": result["confirmed"],
        }
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        if code == "UsernameExistsException":
            raise HTTPException(
                status_code=409, detail="An account with this email already exists."
            )
        elif code == "InvalidPasswordException":
            raise HTTPException(status_code=400, detail=msg)
        else:
            raise HTTPException(status_code=400, detail=msg)


@router.post("/confirm")
async def confirm_signup(req: ConfirmRequest):
    """Confirm a user's email with the verification code from Cognito."""
    if not _cognito:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    try:
        _cognito.confirm_sign_up(
            email=req.email.strip(),
            confirmation_code=req.confirmation_code.strip(),
        )
        return {"message": "Email confirmed. You can now login."}
    except ClientError as e:
        code = e.response["Error"]["Code"]
        msg = e.response["Error"]["Message"]
        if code == "CodeMismatchException":
            raise HTTPException(status_code=400, detail="Invalid verification code.")
        elif code == "ExpiredCodeException":
            raise HTTPException(
                status_code=400, detail="Verification code has expired. Please request a new one."
            )
        else:
            raise HTTPException(status_code=400, detail=msg)


@router.post("/login", response_model=LoginResponse)
async def login(req: LoginRequest):
    """Authenticate a user and return Cognito JWT tokens."""
    if not _cognito:
        raise HTTPException(status_code=503, detail="Auth service not initialized")

    try:
        user = _cognito.sign_in(
            email=req.email.strip(),
            password=req.password,
        )
    except ClientError as e:
        code = e.response["Error"]["Code"]
        if code in ("NotAuthorizedException", "UserNotFoundException"):
            raise HTTPException(status_code=401, detail="Invalid email or password.")
        elif code == "UserNotConfirmedException":
            raise HTTPException(
                status_code=403, detail="Please confirm your email before logging in."
            )
        else:
            raise HTTPException(status_code=400, detail=e.response["Error"]["Message"])

    # Sync user profile to DynamoDB (non-blocking — login succeeds even if this fails)
    if _dynamodb:
        try:
            _dynamodb.save_user_profile(
                user_id=user.user_id,
                email=user.email,
                name=user.name,
                role=user.role,
            )
        except Exception as e:
            logger.warning("dynamodb_profile_sync_failed", error=str(e), user_id=user.user_id)

    return LoginResponse(
        access_token=user.access_token,
        id_token=user.id_token,
        refresh_token=user.refresh_token,
        user={
            "user_id": user.user_id,
            "email": user.email,
            "name": user.name,
            "role": user.role,
        },
    )
