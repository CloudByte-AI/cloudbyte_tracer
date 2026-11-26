"""Fail closed if a user supplied redactor fails."""
def apply(value, field, span, hook):
    if hook is None:
        return value
    try:
        return hook(value, field, span)
    except Exception:
        return None
