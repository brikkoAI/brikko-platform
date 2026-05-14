"""OpenAI-compatible error envelope and HTTP error helpers.

OpenAI SDK clients (Python ``openai>=1.0``, JS ``openai`` v4) parse error
responses with shape::

    {"error": {"message": "...", "type": "...", "code": "...", "param": null}}

Any deviation breaks user code, so we normalize every error through this
module. See ``02_Product/04_tech_stack.md`` §4.1 for the full contract.
"""

from __future__ import annotations

from typing import Any

from fastapi import HTTPException
from fastapi.requests import Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel


class ErrorBody(BaseModel):
    """Shape of the inner ``error`` object."""

    message: str
    type: str
    code: str | None = None
    param: str | None = None


class ErrorEnvelope(BaseModel):
    """Outer envelope: ``{"error": {...}}``."""

    error: ErrorBody


class GatewayError(HTTPException):
    """Subclass of HTTPException carrying the OpenAI-style fields.

    We use this everywhere instead of raw ``HTTPException`` so the global
    exception handler can render the correct envelope.
    """

    def __init__(
        self,
        status_code: int,
        message: str,
        type: str,
        code: str | None = None,
        param: str | None = None,
        headers: dict[str, str] | None = None,
    ) -> None:
        self.error_message = message
        self.error_type = type
        self.error_code = code
        self.error_param = param
        super().__init__(status_code=status_code, detail=message, headers=headers)

    def to_envelope(self) -> dict[str, Any]:
        return ErrorEnvelope(
            error=ErrorBody(
                message=self.error_message,
                type=self.error_type,
                code=self.error_code,
                param=self.error_param,
            )
        ).model_dump()


# --- common error factories ---------------------------------------------------


def invalid_request(
    message: str, param: str | None = None, code: str | None = None
) -> GatewayError:
    return GatewayError(
        status_code=400,
        message=message,
        type="invalid_request_error",
        code=code,
        param=param,
    )


def authentication_error(message: str = "Invalid API key.") -> GatewayError:
    return GatewayError(
        status_code=401,
        message=message,
        type="authentication_error",
        code="invalid_api_key",
        headers={"WWW-Authenticate": "Bearer"},
    )


def insufficient_quota(message: str = "Account balance below required threshold.") -> GatewayError:
    return GatewayError(
        status_code=402,
        message=message,
        type="insufficient_quota",
        code="balance_too_low",
    )


def model_not_found(model_id: str) -> GatewayError:
    return GatewayError(
        status_code=404,
        message=f"The model '{model_id}' does not exist or is not available.",
        type="invalid_request_error",
        code="model_not_found",
        param="model",
    )


def upstream_error(message: str = "Upstream provider returned an error.") -> GatewayError:
    return GatewayError(
        status_code=502,
        message=message,
        type="api_error",
        code="upstream_error",
    )


# --- handlers -----------------------------------------------------------------


async def gateway_exception_handler(request: Request, exc: GatewayError) -> JSONResponse:
    """Render GatewayError as the OpenAI-compatible envelope."""
    return JSONResponse(
        status_code=exc.status_code,
        content=exc.to_envelope(),
        headers=exc.headers,
    )


async def http_exception_handler(request: Request, exc: HTTPException) -> JSONResponse:
    """Fallback for raw HTTPException (e.g. raised by FastAPI internals)."""
    body = ErrorEnvelope(
        error=ErrorBody(
            message=str(exc.detail) if exc.detail else "HTTP error",
            type="api_error",
            code=str(exc.status_code),
            param=None,
        )
    ).model_dump()
    return JSONResponse(status_code=exc.status_code, content=body, headers=exc.headers)


async def validation_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Render Pydantic validation errors as invalid_request_error."""
    # Late import to avoid hard dep at module load
    from fastapi.exceptions import RequestValidationError

    if isinstance(exc, RequestValidationError):
        first = exc.errors()[0] if exc.errors() else {}
        msg = first.get("msg", "Invalid request body")
        loc = first.get("loc", [])
        param = ".".join(str(x) for x in loc[1:]) if len(loc) > 1 else None
        body = ErrorEnvelope(
            error=ErrorBody(
                message=msg,
                type="invalid_request_error",
                code="invalid_body",
                param=param,
            )
        ).model_dump()
        return JSONResponse(status_code=400, content=body)

    body = ErrorEnvelope(
        error=ErrorBody(message="Internal server error", type="api_error", code="internal_error")
    ).model_dump()
    return JSONResponse(status_code=500, content=body)
