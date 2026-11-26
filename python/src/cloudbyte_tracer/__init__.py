"""CloudByte tracing. Core has no runtime dependencies."""
from ._version import SDK_VERSION as __version__
from .core import Tracer, Span
from .context import bind_context
_default = None

def init(api_key=None, base_url=None, **options):
    global _default
    if _default is not None:
        _default.shutdown()
    _default = Tracer(api_key, base_url, **options)
    return _default

def get_tracer():
    global _default
    if _default is None:
        _default = Tracer()
    return _default

def trace(fn=None, *, name=None, kind="custom"):
    def decorate(func):
        # Resolve the process default at call time so decorators work before init().
        import functools, inspect
        if inspect.iscoroutinefunction(func):
            @functools.wraps(func)
            async def wrapped(*args, **kwargs):
                with get_tracer().span(name or func.__name__, kind=kind):
                    return await func(*args, **kwargs)
        else:
            @functools.wraps(func)
            def wrapped(*args, **kwargs):
                with get_tracer().span(name or func.__name__, kind=kind):
                    return func(*args, **kwargs)
        return wrapped
    return decorate(fn) if fn is not None else decorate

def flush(timeout=5.0):
    return get_tracer().flush(timeout)

def shutdown():
    if _default is not None:
        _default.shutdown()

__all__ = ["Tracer", "Span", "init", "get_tracer", "trace", "flush", "shutdown", "bind_context"]
