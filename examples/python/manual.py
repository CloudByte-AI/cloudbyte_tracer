"""Set CLOUDBYTE_AGENT_KEY and CLOUDBYTE_BASE_URL to observe this run."""
from cloudbyte_tracer import init
tracer=init(service_name="example-agent")
with tracer.span("handle_request",kind="agent",input={"question":"hello"}) as root:
    with tracer.span("lookup",kind="tool") as step:
        step.set_output({"answer":"Hello from the manual example"})
    root.set_output("Hello from the manual example")
tracer.shutdown()
print("Manual example finished")
