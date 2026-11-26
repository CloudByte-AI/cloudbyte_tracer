"""Wire sanitation. Keep the existing server's truncation detection working."""
import json, math
from .redact import apply

MAX_FIELD_BYTES = 65536
MAX_ATTRIBUTES_BYTES = 32768

def dumps(value):
    return json.dumps(value, ensure_ascii=False, allow_nan=False, separators=(",", ":"), default=str)

def fit(value):
    try:
        text = dumps(value)
        if len(text.encode("utf-8", "replace")) <= MAX_FIELD_BYTES:
            return json.loads(text), False
    except Exception:
        try:
            text = repr(value)
        except Exception:
            text = "<unserializable>"
    # The server ignores incoming *_truncated. A bounded 70 KB prefix triggers its
    # own flag, just like the Preview helper. See docs/configuration.md.
    return text[:70000].encode("utf-8", "replace")[:70000].decode("utf-8", "ignore"), True

def encode(span, capture_content=True, redact=None, service_name=None):
    try:
        copy = dict(span)
        for field in ("input", "output"):
            if field not in copy:
                continue
            raw = copy.pop(field)
            value = apply(raw, field, span, redact) if capture_content else None
            if value is not None:
                copy[field], truncated = fit(value)
                if truncated:
                    copy[field + "_truncated"] = True
        attrs = dict(copy.get("attributes") or {})
        if service_name:
            attrs["service.name"] = service_name
        cleaned = {}
        for k, v in attrs.items():
            if isinstance(v, str):
                v = apply(v, "attributes." + str(k), span, redact)
            if isinstance(v, (str, bool)) or isinstance(v, (int, float)) and math.isfinite(v):
                cleaned[str(k)] = v
        for key in sorted(cleaned, key=lambda k: len(dumps({k: cleaned[k]}).encode("utf-8", "replace")), reverse=True):
            if len(dumps(cleaned).encode("utf-8", "replace")) <= MAX_ATTRIBUTES_BYTES:
                break
            del cleaned[key]
        if cleaned:
            copy["attributes"] = cleaned
        else:
            copy.pop("attributes", None)
        if copy.get("error"):
            copy["error"] = {"type": str(copy["error"].get("type", "Error")), "message": str(copy["error"].get("message", ""))[:4096]}
        usage = {}
        for k, v in (copy.get("usage") or {}).items():
            if k in ("input_tokens", "output_tokens", "cache_read_tokens", "cache_write_tokens") and isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and v >= 0 and int(v) == v:
                usage[k] = min(int(v), 2147483647)
        if usage:
            copy["usage"] = usage
        else:
            copy.pop("usage", None)
        copy["name"] = str(copy.get("name", "span"))[:200] or "span"
        if copy.get("model") is not None:
            copy["model"] = str(copy["model"])[:200]
        return json.dumps(copy, allow_nan=False, default=str)
    except Exception:
        return None
