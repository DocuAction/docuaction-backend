"""
Standardized Error Response Handler
ALL errors return consistent JSON format. NEVER expose stack traces.
Format: {"error": "message", "code": "ERROR_CODE", "request_id": "uuid"}
"""
import uuid
import logging
import traceback
from fastapi import Request
from fastapi.responses import JSONResponse
from starlette.middleware.base import BaseHTTPMiddleware

logger = logging.getLogger("docuaction.errors")


def create_error_response(status_code: int, error: str, code: str, request_id: str = None):
    """Create standardized error JSON response."""
    return JSONResponse(
        status_code=status_code,
        content={
            "error": error,
            "code": code,
            "request_id": request_id or str(uuid.uuid4()),
        },
    )


# Standard error codes
ERROR_CODES = {
    400: "BAD_REQUEST",
    401: "UNAUTHORIZED",
    403: "FORBIDDEN",
    404: "NOT_FOUND",
    409: "CONFLICT",
    413: "PAYLOAD_TOO_LARGE",
    422: "VALIDATION_ERROR",
    429: "RATE_LIMIT_EXCEEDED",
    500: "INTERNAL_ERROR",
    502: "BAD_GATEWAY",
    503: "SERVICE_UNAVAILABLE",
}


class ErrorHandlerMiddleware(BaseHTTPMiddleware):
    """
    Global error handler middleware.
    Catches all unhandled exceptions and returns standardized JSON.
    NEVER exposes internal stack traces to the client.
    """
    async def dispatch(self, request: Request, call_next):
        request_id = str(uuid.uuid4())

        try:
            response = await call_next(request)

            # Add request ID to all responses
            response.headers["X-Request-ID"] = request_id
            return response

        except Exception as e:
            # Log full stack trace INTERNALLY
            logger.error(
                f"Unhandled error | request_id={request_id} | "
                f"path={request.url.path} | method={request.method} | "
                f"error={str(e)}\n{traceback.format_exc()}"
            )

            # Return safe error to client — NO stack trace
            return create_error_response(
                status_code=500,
                error="An internal error occurred. Please try again or contact support.",
                code="INTERNAL_ERROR",
                request_id=request_id,
            )


def register_exception_handlers(app):
    """Register custom exception handlers on the FastAPI app."""
    from fastapi.exceptions import RequestValidationError
    from starlette.exceptions import HTTPException as StarletteHTTPException

    @app.exception_handler(StarletteHTTPException)
    async def http_exception_handler(request: Request, exc: StarletteHTTPException):
        request_id = str(uuid.uuid4())
        code = ERROR_CODES.get(exc.status_code, "ERROR")
        return create_error_response(
            status_code=exc.status_code,
            error=str(exc.detail),
            code=code,
            request_id=request_id,
        )

    @app.exception_handler(RequestValidationError)
    async def validation_exception_handler(request: Request, exc: RequestValidationError):
        request_id = str(uuid.uuid4())
        # Simplify validation errors — don't expose internal field paths
        errors = []
        for err in exc.errors():
            field = " → ".join(str(loc) for loc in err.get("loc", []))
            errors.append(f"{field}: {err.get('msg', 'invalid')}")
        return create_error_response(
            status_code=422,
            error="; ".join(errors),
            code="VALIDATION_ERROR",
            request_id=request_id,
        )

    @app.exception_handler(Exception)
    async def generic_exception_handler(request: Request, exc: Exception):
        request_id = str(uuid.uuid4())
        logger.error(f"Unhandled: request_id={request_id} error={exc}\n{traceback.format_exc()}")
        return create_error_response(
            status_code=500,
            error="An internal error occurred. Please try again or contact support.",
            code="INTERNAL_ERROR",
            request_id=request_id,
        )
