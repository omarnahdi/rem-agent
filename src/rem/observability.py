"""Langfuse/OpenTelemetry observability for Rem's Agno Discord runtime.

External tracing is enabled only when both LANGFUSE_PUBLIC_KEY and
LANGFUSE_SECRET_KEY are configured. Agent/tool spans use OpenInference's Agno
instrumentor; app-specific spans and a Python logging handler add visibility
for moderation, memory operations, Discord processing, and Python exceptions.

By default, model and tool inputs/outputs are redacted. Set
LANGFUSE_CAPTURE_CONTENT=true only when the project owner has explicitly
accepted sending conversation content to Langfuse.
"""
from __future__ import annotations

import asyncio
import base64
import logging
import os
import re
import sys
import threading
import traceback
from contextlib import contextmanager
from typing import Any, Iterator

logger = logging.getLogger(__name__)

_CONFIG_LOCK = threading.RLock()
_CONFIGURED = False
_ENABLED = False
_PROVIDER: Any | None = None
_LOG_HANDLER: logging.Handler | None = None
_ORIGINAL_SYS_EXCEPTHOOK: Any | None = None
_ORIGINAL_THREADING_EXCEPTHOOK: Any | None = None

# Common secret formats and key/value patterns are scrubbed from telemetry.
_SECRET_PATTERNS = (
    re.compile(r"\bsk-[A-Za-z0-9_-]{16,}\b"),
    re.compile(r"\bAIza[0-9A-Za-z_-]{20,}\b"),
    re.compile(r"\b(?:mfa\.[A-Za-z0-9_-]{20,}|[A-Za-z0-9_-]{24,}\.[A-Za-z0-9_-]{6,}\.[A-Za-z0-9_-]{20,})\b"),
    re.compile(r"(?i)(\b[A-Z0-9_]*(?:API[_ -]?KEY|SECRET[_ -]?KEY|ACCESS[_ -]?TOKEN|REFRESH[_ -]?TOKEN|PASSWORD|AUTHORIZATION)\b\s*[:=]\s*)([^\s,;]+)"),
    re.compile(r"(?i)(\bbearer\s+)[A-Za-z0-9._~+/=-]+"),
)

_EXPORTER_LOGGER_PREFIXES = (
    "langfuse",
    "opentelemetry",
    "openinference.instrumentation",
    "httpx",
    "urllib3",
    "requests",
)
_APP_WARNING_PREFIXES = (
    "rem",
    "agno",
    "discord",
    "mem0",
    "google.genai",
    "asyncio",
    "posthog",
)


class _NoopSpan:
    """Small fallback so tracing helpers never break the bot when disabled."""

    def set_attribute(self, *_: Any, **__: Any) -> None:
        return None

    def add_event(self, *_: Any, **__: Any) -> None:
        return None

    def set_status(self, *_: Any, **__: Any) -> None:
        return None

    def get_span_context(self) -> Any:
        return None


def sanitize_telemetry_text(value: object, *, limit: int = 6000) -> str:
    """Bound and redact likely credentials from strings sent to telemetry."""
    text = str(value or "")
    for pattern in _SECRET_PATTERNS[:3]:
        text = pattern.sub("[REDACTED_SECRET]", text)
    # Strip bearer credentials before generic key=value redaction; otherwise
    # "Authorization: Bearer <token>" would redact only the word "Bearer".
    text = _SECRET_PATTERNS[4].sub(r"\1[REDACTED_SECRET]", text)
    text = _SECRET_PATTERNS[3].sub(r"\1[REDACTED_SECRET]", text)
    if len(text) > limit:
        text = text[:limit] + "…[truncated]"
    return text


def build_langfuse_traces_endpoint(base_url: str | None) -> str:
    """Normalize a Langfuse region/base URL to the current OTLP traces endpoint."""
    base = (base_url or "https://cloud.langfuse.com").strip().rstrip("/")
    if not base:
        base = "https://cloud.langfuse.com"
    if base.endswith("/api/public/otel/v1/traces"):
        return base
    if base.endswith("/api/public/otel"):
        return base + "/v1/traces"
    return base + "/api/public/otel/v1/traces"


def should_capture_log(record: logging.LogRecord) -> bool:
    """Capture errors globally and warnings from Rem's runtime dependencies."""
    name = (record.name or "").casefold()
    if name.startswith(_EXPORTER_LOGGER_PREFIXES):
        # Do not send exporter failures back through the same exporter.
        return False
    if record.levelno >= logging.ERROR:
        return True
    if record.levelno < logging.WARNING:
        return False
    if name == "py.warnings":
        return True
    return name.startswith(_APP_WARNING_PREFIXES)


def _span_is_valid(span: Any) -> bool:
    try:
        context = span.get_span_context()
        return bool(context and context.is_valid)
    except Exception:
        return False


def _exception_fields(record: logging.LogRecord) -> dict[str, str | bool]:
    fields: dict[str, str] = {}
    if not record.exc_info:
        return fields
    exc_type, exc, tb = record.exc_info
    if exc_type is not None:
        fields["exception.type"] = f"{exc_type.__module__}.{exc_type.__qualname__}"
    if exc is not None:
        fields["exception.message"] = sanitize_telemetry_text(exc, limit=2000)
    if tb is not None:
        # Keep enough traceback for triage but cap payload size and redact secrets.
        fields["exception.stacktrace"] = sanitize_telemetry_text(
            "".join(traceback.format_exception(exc_type, exc, tb)), limit=6000
        )
    fields["exception.escaped"] = False
    return fields


class LangfuseLoggingHandler(logging.Handler):
    """Send important Python log records into the active trace or an error trace.

    ERROR/CRITICAL records from all application/dependency loggers are included.
    WARNING records are included for Rem, Agno, Discord, Mem0, Google GenAI,
    asyncio, and Python warnings. Exporter internals are excluded to avoid loops.
    """

    def __init__(self, tracer: Any) -> None:
        super().__init__(level=logging.WARNING)
        self._tracer = tracer
        self._local = threading.local()

    def emit(self, record: logging.LogRecord) -> None:
        if not should_capture_log(record) or getattr(self._local, "busy", False):
            return
        self._local.busy = True
        try:
            try:
                message = sanitize_telemetry_text(record.getMessage(), limit=2000)
            except Exception:
                message = "Unable to format log record"
            attributes: dict[str, str | int | bool] = {
                "log.level": record.levelname,
                "log.logger": sanitize_telemetry_text(record.name, limit=200),
                "log.message": message,
                "code.filepath": sanitize_telemetry_text(record.pathname, limit=500),
                "code.lineno": int(record.lineno),
                "thread.name": sanitize_telemetry_text(record.threadName, limit=200),
            }
            attributes.update(_exception_fields(record))

            from opentelemetry import trace
            from opentelemetry.context import Context
            from opentelemetry.trace import Status, StatusCode

            active_span = trace.get_current_span()
            if _span_is_valid(active_span):
                target = active_span
                target.add_event("python.log", attributes=attributes)
                if record.levelno >= logging.ERROR:
                    target.set_status(Status(StatusCode.ERROR, message[:500]))
                return

            # A standalone root span preserves exceptions from background tasks,
            # startup/shutdown, Discord callbacks, and other untraced code paths.
            name = "python.exception" if record.exc_info or record.levelno >= logging.ERROR else "python.warning"
            with self._tracer.start_as_current_span(
                name,
                context=Context(),
                record_exception=False,
                set_status_on_exception=False,
            ) as span:
                span.set_attribute("service.component", "python.logging")
                for key, value in attributes.items():
                    span.set_attribute(key, value)
                if record.levelno >= logging.ERROR:
                    span.set_status(Status(StatusCode.ERROR, message[:500]))
        except Exception:
            # Telemetry must never cause the application's logging call to fail.
            # Do not log this failure through the same handler (would recurse).
            return
        finally:
            self._local.busy = False


@contextmanager
def trace_span(name: str, attributes: dict[str, Any] | None = None) -> Iterator[Any]:
    """Create a low-overhead app span that degrades safely when tracing is off."""
    try:
        from opentelemetry import trace
    except ImportError:
        yield _NoopSpan()
        return

    tracer = trace.get_tracer("rem.observability")
    with tracer.start_as_current_span(
        name,
        attributes={key: value for key, value in (attributes or {}).items() if value is not None},
        record_exception=False,
        set_status_on_exception=False,
    ) as span:
        try:
            yield span
        except Exception as exc:
            # Avoid the default OpenTelemetry exception recorder here because
            # raw exception strings can contain user-provided text or secrets.
            try:
                from opentelemetry.trace import Status, StatusCode

                message = sanitize_telemetry_text(exc, limit=2000)
                span.add_event(
                    "exception",
                    attributes={
                        "exception.type": f"{type(exc).__module__}.{type(exc).__qualname__}",
                        "exception.message": message,
                        "exception.escaped": True,
                        "exception.stacktrace": sanitize_telemetry_text(
                            "".join(traceback.format_exception(type(exc), exc, exc.__traceback__)),
                            limit=6000,
                        ),
                    },
                )
                span.set_status(Status(StatusCode.ERROR, message[:500]))
            except Exception:
                pass
            raise


def _truthy_env(name: str, *, default: bool = False) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().casefold() in {"1", "true", "yes", "on"}


def configure_observability(*, service_name: str = "rem-agent") -> bool:
    """Configure OTLP export to Langfuse and automatic Agno/tool instrumentation.

    Must be called after environment loading and logging.basicConfig, but before
    creating agents. This function is intentionally safe to call more than once.
    """
    global _CONFIGURED, _ENABLED, _PROVIDER, _LOG_HANDLER
    global _ORIGINAL_SYS_EXCEPTHOOK, _ORIGINAL_THREADING_EXCEPTHOOK

    with _CONFIG_LOCK:
        if _CONFIGURED:
            return _ENABLED

        public_key = (os.getenv("LANGFUSE_PUBLIC_KEY") or "").strip()
        secret_key = (os.getenv("LANGFUSE_SECRET_KEY") or "").strip()
        if not public_key or not secret_key:
            logger.info(
                "Langfuse tracing disabled: configure LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY to enable it."
            )
            return False

        try:
            from opentelemetry import trace
            from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
            from opentelemetry.sdk.resources import Resource
            from opentelemetry.sdk.trace import TracerProvider
            from opentelemetry.sdk.trace.export import BatchSpanProcessor
        except ImportError:
            logger.exception(
                "Langfuse credentials are configured but OpenTelemetry dependencies are missing; install the observability extras."
            )
            return False

        base_url = os.getenv("LANGFUSE_BASE_URL") or os.getenv("LANGFUSE_HOST") or "https://cloud.langfuse.com"
        endpoint = build_langfuse_traces_endpoint(base_url)
        auth = base64.b64encode(f"{public_key}:{secret_key}".encode("utf-8")).decode("ascii")
        exporter = OTLPSpanExporter(
            endpoint=endpoint,
            headers={
                "Authorization": f"Basic {auth}",
                "x-langfuse-ingestion-version": "4",
            },
            timeout=5,
        )

        environment = (os.getenv("DEPLOYMENT_ENVIRONMENT") or os.getenv("ENVIRONMENT") or "production").strip()
        resource = Resource.create(
            {
                "service.name": os.getenv("OTEL_SERVICE_NAME", service_name),
                "service.namespace": "rem",
                "service.version": os.getenv("REM_VERSION", "agno-discord"),
                "deployment.environment": environment,
            }
        )

        current_provider = trace.get_tracer_provider()
        if hasattr(current_provider, "add_span_processor") and type(current_provider).__name__ != "ProxyTracerProvider":
            # Respect a provider initialized by the host process while adding the
            # Langfuse exporter to it, so trace context is shared consistently.
            provider = current_provider
            provider.add_span_processor(BatchSpanProcessor(exporter))
        else:
            provider = TracerProvider(resource=resource)
            provider.add_span_processor(BatchSpanProcessor(exporter))
            trace.set_tracer_provider(provider)
            # If another library set the global provider first, its provider wins.
            actual_provider = trace.get_tracer_provider()
            if actual_provider is not provider:
                if hasattr(actual_provider, "add_span_processor"):
                    actual_provider.add_span_processor(BatchSpanProcessor(exporter))
                    provider = actual_provider
                else:
                    raise RuntimeError("Could not register the Langfuse OpenTelemetry tracer provider.")

        # Instrument Agno runs/model/tool spans. Redact inputs and outputs by
        # default: traces retain topology, model/tool names, timings, and statuses.
        instrumentor_active = False
        try:
            from openinference.instrumentation import TraceConfig
            from openinference.instrumentation.agno import AgnoInstrumentor

            capture_content = _truthy_env("LANGFUSE_CAPTURE_CONTENT", default=False)
            config = TraceConfig(
                hide_inputs=not capture_content,
                hide_outputs=not capture_content,
                hide_input_messages=not capture_content,
                hide_output_messages=not capture_content,
                hide_input_images=True,
            )
            AgnoInstrumentor().instrument(tracer_provider=provider, config=config)
            instrumentor_active = True
        except Exception:
            # Retain custom spans + exception telemetry even if auto-instrumentor
            # is incompatible; make the incomplete instrumentation explicit.
            logger.exception("Agno auto-instrumentation could not be enabled; app spans and Python exceptions remain enabled.")

        logger_root = logging.getLogger()
        logger_root.addHandler(LangfuseLoggingHandler(trace.get_tracer("rem.python.logging")))
        _LOG_HANDLER = next(
            (handler for handler in logger_root.handlers if isinstance(handler, LangfuseLoggingHandler)),
            None,
        )
        logging.captureWarnings(True)

        # Uncaught sync/thread exceptions are routed through logging so the
        # telemetry handler can produce standalone error observations.
        _ORIGINAL_SYS_EXCEPTHOOK = sys.excepthook
        _ORIGINAL_THREADING_EXCEPTHOOK = threading.excepthook

        def sys_exception_hook(exc_type: Any, exc: BaseException, tb: Any) -> None:
            logging.getLogger("rem.unhandled").critical(
                "Uncaught main-thread exception",
                exc_info=(exc_type, exc, tb),
            )
            if _ORIGINAL_SYS_EXCEPTHOOK is not None:
                _ORIGINAL_SYS_EXCEPTHOOK(exc_type, exc, tb)

        def thread_exception_hook(args: Any) -> None:
            logging.getLogger("rem.unhandled").critical(
                "Uncaught thread exception | thread=%s",
                getattr(args.thread, "name", "unknown"),
                exc_info=(args.exc_type, args.exc_value, args.exc_traceback),
            )
            if _ORIGINAL_THREADING_EXCEPTHOOK is not None:
                _ORIGINAL_THREADING_EXCEPTHOOK(args)

        sys.excepthook = sys_exception_hook
        threading.excepthook = thread_exception_hook

        _PROVIDER = provider
        _CONFIGURED = True
        _ENABLED = True
        logger.info(
            "Langfuse observability enabled | endpoint=%s | service=%s | agno_instrumentation=%s | content_capture=%s",
            endpoint,
            resource.attributes.get("service.name", service_name),
            instrumentor_active,
            _truthy_env("LANGFUSE_CAPTURE_CONTENT", default=False),
        )
        return True


def install_asyncio_exception_handler(loop: asyncio.AbstractEventLoop | None = None) -> bool:
    """Capture otherwise-unretrieved asyncio task exceptions into Langfuse."""
    if not _ENABLED:
        return False
    try:
        loop = loop or asyncio.get_running_loop()
        current = loop.get_exception_handler()
        if getattr(current, "__rem_langfuse_handler__", False):
            return True

        def exception_handler(event_loop: asyncio.AbstractEventLoop, context: dict[str, Any]) -> None:
            exception = context.get("exception")
            if isinstance(exception, BaseException):
                logger.error(
                    "Unhandled asyncio exception | message=%s",
                    context.get("message", "asyncio task failed"),
                    exc_info=(type(exception), exception, exception.__traceback__),
                )
            else:
                logger.error(
                    "Unhandled asyncio event-loop error | message=%s | context_keys=%s",
                    context.get("message", "unknown asyncio failure"),
                    ",".join(sorted(str(key) for key in context.keys() if key not in {"task", "future", "exception"})),
                )

        setattr(exception_handler, "__rem_langfuse_handler__", True)
        loop.set_exception_handler(exception_handler)
        logger.info("Asyncio loop exception handler connected to Langfuse telemetry")
        return True
    except Exception:
        logger.exception("Could not install asyncio exception handler")
        return False


def shutdown_observability() -> None:
    """Flush traces before process exit; never prevent the bot from shutting down."""
    global _ENABLED
    provider = _PROVIDER
    if provider is None:
        return
    try:
        provider.force_flush(timeout_millis=5000)
    except Exception:
        logger.exception("Failed to flush pending Langfuse spans during shutdown")
    try:
        provider.shutdown()
    except Exception:
        logger.exception("Failed to shut down the Langfuse tracer provider")
    _ENABLED = False
