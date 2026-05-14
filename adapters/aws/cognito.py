# adapters/aws/cognito.py
"""AWS Cognito adapter for user authentication and registration."""

import boto3
import structlog
from botocore.exceptions import ClientError
from pydantic import BaseModel

logger = structlog.get_logger(__name__)


class CognitoConfig(BaseModel):
    """Configuration for Cognito User Pool."""

    region: str = "us-east-1"
    user_pool_id: str
    app_client_id: str


class CognitoUser(BaseModel):
    """Authenticated user returned after successful login."""

    user_id: str  # Cognito 'sub'
    email: str
    name: str
    role: str
    access_token: str
    id_token: str
    refresh_token: str


class CognitoAdapter:
    """Wraps AWS Cognito Identity Provider for signup, login, and user management."""

    def __init__(self, config: CognitoConfig):
        self.config = config
        self.client = boto3.client(
            "cognito-idp",
            region_name=config.region,
        )
        logger.info("cognito_adapter_initialized", user_pool_id=config.user_pool_id)

    def sign_up(self, email: str, password: str, name: str, role: str = "agent") -> dict:
        """Register a new user in Cognito."""
        try:
            response = self.client.sign_up(
                ClientId=self.config.app_client_id,
                Username=email,
                Password=password,
                UserAttributes=[
                    {"Name": "email", "Value": email},
                    {"Name": "name", "Value": name},
                    {"Name": "custom:role", "Value": role},
                ],
            )
            logger.info("cognito_signup_success", email=email, role=role)
            return {
                "user_sub": response["UserSub"],
                "confirmed": response["UserConfirmed"],
            }
        except ClientError as e:
            code = e.response["Error"]["Code"]
            message = e.response["Error"]["Message"]
            logger.error("cognito_signup_failed", email=email, error_code=code, error=message)
            raise

    def confirm_sign_up(self, email: str, confirmation_code: str) -> bool:
        """Confirm a user's email with the verification code."""
        try:
            self.client.confirm_sign_up(
                ClientId=self.config.app_client_id,
                Username=email,
                ConfirmationCode=confirmation_code,
            )
            logger.info("cognito_confirm_success", email=email)
            return True
        except ClientError as e:
            logger.error("cognito_confirm_failed", email=email, error=str(e))
            raise

    def sign_in(self, email: str, password: str) -> CognitoUser:
        """Authenticate a user and return JWT tokens."""
        try:
            response = self.client.initiate_auth(
                ClientId=self.config.app_client_id,
                AuthFlow="USER_PASSWORD_AUTH",
                AuthParameters={
                    "USERNAME": email,
                    "PASSWORD": password,
                },
            )
            tokens = response["AuthenticationResult"]

            # Fetch user attributes to build the CognitoUser object
            user_info = self.client.get_user(
                AccessToken=tokens["AccessToken"],
            )
            attrs = {a["Name"]: a["Value"] for a in user_info["UserAttributes"]}

            user = CognitoUser(
                user_id=attrs.get("sub", ""),
                email=attrs.get("email", email),
                name=attrs.get("name", ""),
                role=attrs.get("custom:role", "agent"),
                access_token=tokens["AccessToken"],
                id_token=tokens["IdToken"],
                refresh_token=tokens.get("RefreshToken", ""),
            )
            logger.info("cognito_signin_success", email=email, role=user.role)
            return user

        except ClientError as e:
            code = e.response["Error"]["Code"]
            logger.error("cognito_signin_failed", email=email, error_code=code)
            raise

    def get_user(self, access_token: str) -> dict:
        """Get user profile from an access token."""
        try:
            response = self.client.get_user(AccessToken=access_token)
            attrs = {a["Name"]: a["Value"] for a in response["UserAttributes"]}
            return {
                "user_id": attrs.get("sub", ""),
                "email": attrs.get("email", ""),
                "name": attrs.get("name", ""),
                "role": attrs.get("custom:role", "agent"),
            }
        except ClientError as e:
            logger.error("cognito_get_user_failed", error=str(e))
            raise

    def admin_set_role(self, email: str, role: str) -> None:
        """Admin: update a user's role attribute."""
        try:
            self.client.admin_update_user_attributes(
                UserPoolId=self.config.user_pool_id,
                Username=email,
                UserAttributes=[{"Name": "custom:role", "Value": role}],
            )
            logger.info("cognito_role_updated", email=email, new_role=role)
        except ClientError as e:
            logger.error("cognito_role_update_failed", email=email, error=str(e))
            raise
