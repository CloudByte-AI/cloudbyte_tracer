from opentelemetry.sdk.trace import SpanProcessor
from .exporter import CloudByteSpanExporter
from .mapping import map_span

class CloudByteSpanProcessor(SpanProcessor):
    def __init__(self, tracer):
        self.tracer = tracer
        self.exporter = CloudByteSpanExporter(tracer)
        self.enabled = True
        self.parents = {}

    def on_start(self, span, parent_context=None):
        if self.enabled:
            self.parents[span.get_span_context().span_id] = span.parent.span_id if span.parent else None
        if self.enabled and (not span.parent or not span.parent.is_valid):
            try:
                self.tracer._enqueue(map_span(span, start=True))
            except Exception:
                pass

    def on_end(self, span):
        if self.enabled:
            self.exporter.export((span,))
            self.parents.pop(span.get_span_context().span_id, None)

    def shutdown(self):
        self.enabled = False
        self.parents.clear()

    def force_flush(self, timeout_millis=30000):
        return self.exporter.force_flush(timeout_millis)
