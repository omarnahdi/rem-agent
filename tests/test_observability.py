import logging
import sys
import unittest

from opentelemetry.sdk.trace import TracerProvider
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from opentelemetry.trace import StatusCode

from rem.observability import (
    LangfuseLoggingHandler,
    build_langfuse_traces_endpoint,
    sanitize_telemetry_text,
    should_capture_log,
    trace_span,
)


class LangfuseEndpointTests(unittest.TestCase):
    def test_eu_region_base_url(self):
        self.assertEqual(
            build_langfuse_traces_endpoint("https://cloud.langfuse.com"),
            "https://cloud.langfuse.com/api/public/otel/v1/traces",
        )

    def test_us_region_base_url(self):
        self.assertEqual(
            build_langfuse_traces_endpoint("https://us.cloud.langfuse.com/"),
            "https://us.cloud.langfuse.com/api/public/otel/v1/traces",
        )

    def test_existing_otel_path_is_not_duplicated(self):
        self.assertEqual(
            build_langfuse_traces_endpoint("https://example.test/api/public/otel"),
            "https://example.test/api/public/otel/v1/traces",
        )

    def test_existing_complete_path_is_not_duplicated(self):
        self.assertEqual(
            build_langfuse_traces_endpoint("https://example.test/api/public/otel/v1/traces"),
            "https://example.test/api/public/otel/v1/traces",
        )


class TelemetryRedactionTests(unittest.TestCase):
    def test_common_keys_are_redacted(self):
        text = sanitize_telemetry_text(
            "BRAVE_API_KEY=brave-secret-123 LANGFUSE_SECRET_KEY=lf-secret-123"
        )
        self.assertNotIn("brave-secret-123", text)
        self.assertNotIn("lf-secret-123", text)
        self.assertIn("[REDACTED_SECRET]", text)

    def test_bearer_and_known_key_formats_are_redacted(self):
        text = sanitize_telemetry_text(
            "Authorization: Bearer abc.def.ghi sk-0123456789abcdefghijkl AIza0123456789abcdefghijklmn"
        )
        self.assertNotIn("abc.def.ghi", text)
        self.assertNotIn("sk-0123456789abcdefghijkl", text)
        self.assertNotIn("AIza0123456789abcdefghijklmn", text)

    def test_long_payload_is_bounded(self):
        text = sanitize_telemetry_text("x" * 1000, limit=50)
        self.assertLessEqual(len(text), 70)
        self.assertTrue(text.endswith("…[truncated]"))


class LogCapturePolicyTests(unittest.TestCase):
    @staticmethod
    def record(name: str, level: int) -> logging.LogRecord:
        return logging.LogRecord(name, level, "file.py", 1, "message", (), None)

    def test_application_errors_are_captured(self):
        self.assertTrue(should_capture_log(self.record("rem.discord", logging.ERROR)))
        self.assertTrue(should_capture_log(self.record("third_party.client", logging.ERROR)))

    def test_app_warnings_are_captured(self):
        self.assertTrue(should_capture_log(self.record("agno.agent", logging.WARNING)))
        self.assertTrue(should_capture_log(self.record("mem0.memory", logging.WARNING)))
        self.assertTrue(should_capture_log(self.record("posthog", logging.WARNING)))

    def test_unrelated_debug_and_warning_are_not_captured(self):
        self.assertFalse(should_capture_log(self.record("rem.discord", logging.DEBUG)))
        self.assertFalse(should_capture_log(self.record("urllib3.connectionpool", logging.WARNING)))
        self.assertFalse(should_capture_log(self.record("random.library", logging.WARNING)))

    def test_exporter_logs_are_excluded_to_prevent_recursion(self):
        self.assertFalse(should_capture_log(self.record("opentelemetry.exporter.otlp", logging.ERROR)))
        self.assertFalse(should_capture_log(self.record("langfuse.client", logging.ERROR)))


class SafeSpanHelperTests(unittest.TestCase):
    def test_span_helper_does_not_break_application_flow(self):
        with trace_span("test.operation", {"test.attribute": "value"}) as span:
            span.set_attribute("test.completed", True)


class PythonExceptionSpanTests(unittest.TestCase):
    def setUp(self):
        self.exporter = InMemorySpanExporter()
        self.provider = TracerProvider()
        self.provider.add_span_processor(SimpleSpanProcessor(self.exporter))
        self.tracer = self.provider.get_tracer("test.rem")
        self.handler = LangfuseLoggingHandler(self.tracer)

    def tearDown(self):
        self.provider.shutdown()

    def test_exception_log_is_attached_to_active_span_and_redacted(self):
        with self.tracer.start_as_current_span("agent.request") as parent:
            try:
                raise RuntimeError("BRAVE_API_KEY=secret-value")
            except RuntimeError:
                record = logging.LogRecord(
                    "rem.agent", logging.ERROR, "agent.py", 42,
                    "Agent request failed | request_id=%s", ("req-123",), sys.exc_info(),
                )
                self.handler.emit(record)
            self.assertEqual(parent.status.status_code, StatusCode.ERROR)
            self.assertTrue(any(event.name == "python.log" for event in parent.events))
            exception_event = next(event for event in parent.events if event.name == "python.log")
            rendered = repr(exception_event.attributes)
            self.assertNotIn("secret-value", rendered)
            self.assertIn("RuntimeError", rendered)

    def test_unscoped_exception_becomes_standalone_error_span(self):
        try:
            raise ValueError("failure without active trace")
        except ValueError:
            record = logging.LogRecord(
                "rem.background", logging.ERROR, "memory_service.py", 10,
                "Background task failed", (), sys.exc_info(),
            )
            self.handler.emit(record)
        spans = self.exporter.get_finished_spans()
        self.assertEqual(len(spans), 1)
        self.assertEqual(spans[0].name, "python.exception")
        self.assertEqual(spans[0].status.status_code, StatusCode.ERROR)
        self.assertIn("ValueError", repr(spans[0].attributes))


if __name__ == "__main__":
    unittest.main()
