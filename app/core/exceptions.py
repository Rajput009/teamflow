class AppError(Exception):
    """Base class for domain errors.

    Services raise these; the HTTP layer translates them. This keeps business
    logic free of FastAPI imports — a Celery worker can call the same service
    and catch the same exceptions.
    """

    status_code = 500
    code = "INTERNAL_ERROR"
    message = "An unexpected error occurred."

    def __init__(
        self,
        message: str | None = None,
        *,
        details: list[dict] | None = None,
    ) -> None:
        self.details = details or []
        if message:
            self.message = message
        super().__init__(self.message)


class ConflictError(AppError):
    status_code = 409
    code = "CONFLICT"


class ValidationError(AppError):
    status_code = 422
    code = "VALIDATION_ERROR"


class NotFoundError(AppError):
    status_code = 404
    code = "NOT_FOUND"


class ForbiddenError(AppError):
    status_code = 403
    code = "FORBIDDEN"
    message = "You do not have permission to perform this action."


class EmailAlreadyExistsError(ConflictError):
    code = "EMAIL_ALREADY_EXISTS"
    message = "A user with this email already exists."


class InvalidCredentialsError(AppError):
    status_code = 401
    code = "INVALID_CREDENTIALS"
    message = "Incorrect email or password."


class InvalidRefreshTokenError(AppError):
    status_code = 401
    code = "INVALID_REFRESH_TOKEN"
    message = "Invalid or expired refresh token."


class NotAuthenticatedError(AppError):
    status_code = 401
    code = "NOT_AUTHENTICATED"
    message = "Not authenticated."


class InvalidAccessTokenError(AppError):
    status_code = 401
    code = "INVALID_TOKEN"
    message = "Invalid or expired access token."


class AccountDisabledError(AppError):
    status_code = 401
    code = "ACCOUNT_DISABLED"
    message = "This account has been deactivated."


class NoOrganizationError(AppError):
    status_code = 403
    code = "NO_ORGANIZATION"
    message = "You are not a member of any organization."


class AlreadyMemberError(ConflictError):
    code = "ALREADY_MEMBER"
    message = "This user is already a member of the organization."


class UserNotFoundError(ValidationError):
    code = "USER_NOT_FOUND"
    message = "No registered user found with this email."


class LastOwnerError(ConflictError):
    code = "LAST_OWNER"
    message = "An organization must keep at least one owner."


class ForbiddenRoleChangeError(ForbiddenError):
    message = "Owners can only be managed by other owners."


class ProjectNameExistsError(ConflictError):
    code = "PROJECT_NAME_EXISTS"
    message = "A project with this name already exists in your organization."


class UserNotOrgMemberError(ValidationError):
    code = "USER_NOT_ORG_MEMBER"
    message = "The target user is not a member of this organization."


class AlreadyProjectMemberError(ConflictError):
    code = "ALREADY_PROJECT_MEMBER"
    message = "This user is already a member of the project."


# --- AI layer (docs/features/11-ai-task-generator.md) ---


class AiNotConfiguredError(AppError):
    status_code = 503
    code = "AI_NOT_CONFIGURED"
    message = "AI features are not configured on this deployment."


class AiUpstreamError(AppError):
    status_code = 502
    code = "AI_UPSTREAM_ERROR"
    message = "The AI provider could not be reached. Try again shortly."


class AiInvalidOutputError(AiUpstreamError):
    code = "AI_INVALID_OUTPUT"
    message = "The AI returned an unusable response. Try rephrasing your request."
