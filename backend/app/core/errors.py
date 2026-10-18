import logging

import httpx

from fastapi import FastAPI, Request, status
from fastapi.responses import JSONResponse
from postgrest.exceptions import APIError

logger = logging.getLogger(__name__)

# Postgres error codes surfaced by PostgREST -> HTTP status.
_PG_STATUS = {
    "42501": status.HTTP_403_FORBIDDEN,  # insufficient_privilege (role/tenant triggers)
    "23505": status.HTTP_409_CONFLICT,  # unique_violation
    "23503": status.HTTP_409_CONFLICT,  # foreign_key_violation
    "23514": 422,  # check_violation
    "23502": 422,  # not_null_violation
    "22P02": 422,  # invalid_text_representation (bad uuid/enum)
}


class NotFound(Exception):
    def __init__(self, what: str):
        self.what = what


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(httpx.TransportError)
    async def upstream_transport_error(_: Request, exc: httpx.TransportError) -> JSONResponse:
        # A handled response passes through CORS and gives the browser a useful message.
        # Do not retry writes: the upstream may already have committed them.
        logger.warning("Upstream connection failed: %s", type(exc).__name__)
        return JSONResponse(status_code=503, content={
            "detail": "The database connection was interrupted. Reload to check the latest state before retrying.",
            "code": "upstream_connection_interrupted",
        })

    @app.exception_handler(NotFound)
    async def not_found(_: Request, exc: NotFound) -> JSONResponse:
        return JSONResponse(status_code=404, content={"detail": f"{exc.what} not found"})

    @app.exception_handler(APIError)
    async def postgrest_error(_: Request, exc: APIError) -> JSONResponse:
        code = exc.code or ""
        http_status = _PG_STATUS.get(code, status.HTTP_502_BAD_GATEWAY)
        if http_status == status.HTTP_502_BAD_GATEWAY:
            logger.error("Database error %s: %s", code, exc.message)
        return JSONResponse(status_code=http_status, content={"detail": exc.message, "code": code})
