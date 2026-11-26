"""Bridge callback-based LangChain spans into ordinary OTel parent context."""
from contextlib import contextmanager
from opentelemetry import trace

class ContextTracer:
    def __init__(self, tracer, parents):
        self._tracer, self._parents = tracer, parents

    def _context(self, context):
        try:
            from openinference.instrumentation.langchain import get_current_span
            candidate = get_current_span()
            if candidate is None:
                return context
            current = trace.get_current_span(context).get_span_context()
            cursor = candidate.get_span_context().span_id
            seen = set()
            while cursor and cursor not in seen:
                if cursor == current.span_id:
                    return trace.set_span_in_context(candidate, context)
                seen.add(cursor)
                cursor = self._parents.get(cursor)
        except Exception:
            pass
        return context

    def start_span(self, name, context=None, **kwargs):
        return self._tracer.start_span(name, context=self._context(context), **kwargs)

    @contextmanager
    def start_as_current_span(self, name, context=None, **kwargs):
        with self._tracer.start_as_current_span(name, context=self._context(context), **kwargs) as span:
            yield span

class ContextProvider:
    def __init__(self, provider, parents):
        self.provider, self.parents = provider, parents
    def get_tracer(self, *args, **kwargs):
        return ContextTracer(self.provider.get_tracer(*args, **kwargs), self.parents)
