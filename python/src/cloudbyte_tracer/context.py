"""Threads do not inherit Python context. Copy it explicitly when submitting work."""
from contextvars import copy_context

def bind_context(fn):
    context = copy_context()
    def run(*args, **kwargs):
        return context.copy().run(fn, *args, **kwargs)
    return run
