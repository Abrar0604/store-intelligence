"""
Structured JSON logging with request tracing.
Every request logs: trace_id, store_id, endpoint, latency_ms, event_count, status_code.
"""

import logging
import json
import sys
import uuid
import time
from typing import Callable
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class JSONFormatter(logging.Formatter):
    """Format log records as JSON for structured logging."""

    def format(self, record):
        log_entry = {
            "timestamp": self.formatTime(record),
            "level": record.levelname,
            "logger": record.name,
            "message": record.getMessage(),
        }
        # Add extra fields if present
        for key in ("trace_id", "store_id", "endpoint", "latency_ms",
                     "event_count", "status_code", "method", "path"):
            if hasattr(record, key):
                log_entry[key] = getattr(record, key)
        
        if record.exc_info and record.exc_info[0]:
            log_entry["exception"] = self.formatException(record.exc_info)

        return json.dumps(log_entry)


def setup_logging(level: str = "INFO"):
    """Configure structured JSON logging."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JSONFormatter())
    
    root_logger = logging.getLogger()
    root_logger.setLevel(getattr(logging, level.upper(), logging.INFO))
    root_logger.handlers = [handler]

    # Suppress noisy library loggers
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.error").setLevel(logging.INFO)


class RequestLoggingMiddleware(BaseHTTPMiddleware):
    """
    Middleware that logs every API request with structured fields:
    trace_id, method, path, status_code, latency_ms.
    
    Also injects trace_id into request state for downstream use.
    """

    async def dispatch(self, request: Request, call_next: Callable) -> Response:
        # Generate or extract trace ID
        trace_id = request.headers.get("X-Trace-ID", str(uuid.uuid4())[:8])
        request.state.trace_id = trace_id
        
        start_time = time.monotonic()
        
        try:
            response = await call_next(request)
        except Exception as e:
            latency_ms = round((time.monotonic() - start_time) * 1000, 2)
            logger = logging.getLogger("api.request")
            logger.error(
                f"{request.method} {request.url.path} → 500",
                extra={
                    "trace_id": trace_id,
                    "method": request.method,
                    "path": request.url.path,
                    "status_code": 500,
                    "latency_ms": latency_ms,
                },
            )
            raise
        
        latency_ms = round((time.monotonic() - start_time) * 1000, 2)
        
        # Extract store_id from path if present
        store_id = None
        path_parts = request.url.path.strip("/").split("/")
        if "stores" in path_parts:
            idx = path_parts.index("stores")
            if idx + 1 < len(path_parts):
                store_id = path_parts[idx + 1]
        
        # Log the request
        logger = logging.getLogger("api.request")
        logger.info(
            f"{request.method} {request.url.path} → {response.status_code} ({latency_ms}ms)",
            extra={
                "trace_id": trace_id,
                "method": request.method,
                "path": request.url.path,
                "status_code": response.status_code,
                "latency_ms": latency_ms,
                "store_id": store_id,
            },
        )
        
        # Add trace ID to response headers
        response.headers["X-Trace-ID"] = trace_id
        return response
