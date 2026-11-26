# Privacy and redaction

Content is enabled by default. The CloudByte organisation's store-content switch separately controls server storage; it does not prevent content leaving your application. To prevent sending content, set capture_content=False (Python), captureContent:false (Node), or CLOUDBYTE_CAPTURE_CONTENT=false. OpenInference hide-inputs/hide-outputs flags are configured too. Content namespaces are removed from generic attributes.

redact(value, field, span) runs before enqueueing input/output and string attributes. field is input, output or attributes.<key>; span is the wire snapshot. Do not mutate it. Throwing or returning None/null/undefined drops the field, with no unredacted fallback. Capture disabled always omits input/output.

Python example: init(redact=lambda value, field, span: "[redacted]" if field == "input" else value). Node: init({redact: (value, field) => field === "input" ? "[redacted]" : value}).

Names, models, numeric attributes and errors are outside the content hook. Keep secrets out of them. With capture enabled, raw content may enter OTel memory before the egress redactor; disable capture to enable upstream hiding as well.

Core data is held only in memory and sent only to the configured endpoint. Redirects are refused to protect Bearer credentials. There are no update/registry checks or extra telemetry endpoints. Never commit keys or captured customer data. Test agent keys are cbagt_ followed by 32 T characters; provider example keys are inert mock placeholders.
