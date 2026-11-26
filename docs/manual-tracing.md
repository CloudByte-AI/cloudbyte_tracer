# Manual tracing

Python: tracer.span(name, kind="custom", input=None, model=None, attributes=None, parent=None) returns a context manager. @trace(kind="tool") and @tracer.trace(kind="tool") wrap sync/async functions. Module decorators resolve the default tracer at invocation, so definitions may precede init. Decorators record timing/errors; set content explicitly.

Node: tracer.withSpan(name, options, callback) supports sync callbacks and promises. tracer.trace(fn, options) preserves the receiver, arguments and return value. Module-level withSpan/trace resolve the tracer from init. Callbacks receive a Span.

Python setters: set_output(value), set_model(name), set_usage(input_tokens=..., output_tokens=..., cache_read_tokens=..., cache_write_tokens=...), set_attribute(key,value), set_status(status), record_error(exception). Node uses camelCase; setUsage takes an object with those same snake_case token keys.

Kinds: agent, llm, tool, retrieval, custom. Status: ok, error, unset. Normal completion defaults to ok. Application exceptions propagate unchanged and mark error. IDs: trace_id/span_id/parent_span_id in Python; traceId/spanId/parentSpanId in Node.

Asyncio tasks and promises inherit context; concurrent branches keep their own parents. Python threads require parent=span or bind_context(fn), which snapshots context at binding and copies it per invocation. asyncio.to_thread propagates context. Node worker threads need application propagation; AsyncLocalStorage covers one Node process thread.

Roots emit start and end copies; children emit only end copies. With auto-capture enabled manual spans use the same OTel provider and can nest under framework steps.
