"""OpenInference to the existing CloudByte JSON contract."""
import datetime, json, re

KIND_MAP = {"LLM": "llm", "TOOL": "tool", "RETRIEVER": "retrieval", "RERANKER": "retrieval", "AGENT": "agent"}
USAGE_MAP = {"prompt": "input_tokens", "completion": "output_tokens", "prompt_details.cache_read": "cache_read_tokens", "prompt_details.cache_write": "cache_write_tokens"}

def timestamp(ns):
    return datetime.datetime.fromtimestamp(ns / 1e9, datetime.timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")

def content(attrs, direction):
    raw = attrs.get(direction + ".value")
    # Message arrays take priority over raw request/response envelopes.
    messages = {}
    pattern = re.compile(r"^llm\." + direction + r"_messages\.(\d+)\.message\.(role|content|contents\.(\d+)\.message_content\.text)$")
    for key, value in attrs.items():
        m = pattern.match(key)
        if not m:
            continue
        item = messages.setdefault(int(m[1]), {})
        if m[2] in ("role", "content"):
            item[m[2]] = value
        else:
            item.setdefault("_parts", {})[int(m[3])] = value
    if messages:
        result = []
        for _, item in sorted(messages.items()):
            parts = item.pop("_parts", {})
            if "content" not in item and parts:
                item["content"] = "".join(str(v) for _, v in sorted(parts.items()))
            result.append(item)
        return result
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            return raw
    return raw

def map_span(span, start=False):
    attrs = dict(span.attributes or {})
    ctx = span.get_span_context()
    parent = span.parent
    parent_id = format(parent.span_id, "016x") if parent and parent.is_valid else None
    oi_kind = str(attrs.get("openinference.span.kind", "")).upper()
    kind = KIND_MAP.get(oi_kind, "agent" if oi_kind == "CHAIN" and parent_id is None else "custom")
    result = {"trace_id": format(ctx.trace_id, "032x"), "span_id": format(ctx.span_id, "016x"), "parent_span_id": parent_id,
              "name": span.name, "kind": kind, "started_at": timestamp(span.start_time), "ended_at": None if start else timestamp(span.end_time)}
    if not start:
        result["status"] = "error" if span.status.status_code.name == "ERROR" else "unset" if span.status.status_code.name == "UNSET" else "ok"
        for event in span.events:
            if event.name == "exception":
                ea = event.attributes or {}
                result["error"] = {"type": str(ea.get("exception.type", "Error")), "message": str(ea.get("exception.message", ""))}
                result["status"] = "error"
        if "error" not in result and result["status"] == "error":
            result["error"] = {"type": "Error", "message": span.status.description or ""}
        usage = {dest: attrs["llm.token_count." + src] for src, dest in USAGE_MAP.items() if "llm.token_count." + src in attrs}
        if usage:
            result["usage"] = usage
    for direction in (("input",) if start else ("input", "output")):
        value = content(attrs, direction)
        if value is not None:
            result[direction] = value
    if "llm.model_name" in attrs:
        result["model"] = attrs["llm.model_name"]
    # Everything in these namespaces can contain prompts, tool arguments, documents,
    # images or parameters. Do not duplicate them as string attributes.
    excluded = ("input.", "output.", "llm.input_messages.", "llm.output_messages.", "llm.tools.", "llm.token_count.", "retrieval.documents.", "embedding.", "llm.invocation_parameters", "tool.parameters", "llm.function_call", "llm.prompt_template.", "gen_ai.input.", "gen_ai.output.", "gen_ai.prompt", "gen_ai.completion")
    result["attributes"] = {k: v for k, v in attrs.items() if k not in ("openinference.span.kind", "llm.model_name") and not k.startswith(excluded)}
    return result
