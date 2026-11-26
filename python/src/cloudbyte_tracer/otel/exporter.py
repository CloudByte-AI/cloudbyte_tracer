from opentelemetry.sdk.trace.export import SpanExporter, SpanExportResult
from .mapping import map_span

class CloudByteSpanExporter(SpanExporter):
    def __init__(self, tracer):
        self.tracer = tracer

    def export(self, spans):
        for span in spans:
            try:
                self.tracer._enqueue(map_span(span))
            except Exception:
                pass
        return SpanExportResult.SUCCESS

    def shutdown(self):
        pass

    def force_flush(self, timeout_millis=30000):
        self.tracer.flush(timeout_millis / 1000)
        return True
