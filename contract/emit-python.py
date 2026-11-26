"""Emit real core span payloads; no HTTP or backend application imports."""
import json, sys
from pathlib import Path
from cloudbyte_tracer import Tracer
from cloudbyte_tracer.limits import encode
root=Path(__file__).resolve().parent
cases=json.loads((root/"cases.json").read_text(encoding="utf8"))
payloads=[json.loads(encode(case["raw"])) for case in cases]
t=Tracer("cbagt_"+"T"*32)
t._enqueue=lambda span: payloads.append(json.loads(t._encode(span)))
with t.span("runtime-root",kind="agent",input={"question":"hi"}) as outer:
    with t.span("runtime-tool",kind="tool") as inner:
        inner.set_output({"answer":"hello"})
t.shutdown()
Path(sys.argv[1]).write_text(json.dumps({"spans":payloads},ensure_ascii=False),encoding="utf8")
