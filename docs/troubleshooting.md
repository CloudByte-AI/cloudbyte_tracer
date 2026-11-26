# Troubleshooting

No runs: check key, base URL, module/agent enabled state and CLOUDBYTE_DISABLED. Missing keys produce a no-op. Inspect cloudbyte_tracer: stderr diagnostics; debug adds rejection counts/indexes, never payloads.

401/403 stops that tracer permanently. Correct access and restart; re-enabling a module alone does not restart it.

Missing final steps: consume/close streams, exit Python context managers, await Node callbacks and flush before serverless returns. Children appear when finished; roots send a start copy. Hard termination cannot flush memory.

Missing auto spans: install selected optional integrations and provider clients, initialise before requests, and pass explicit modules with Node ESM. Competing patchers are skipped. A second auto-capture owner falls back to manual tracing with a warning.

Graph parenting changed after an upgrade: use supported versions and rerun prototype tests. Callback framework context requires the bridge; an isolated provider alone is insufficient.

Missing content: check SDK controls/redactors and CloudByte storage/retention. Oversized content becomes a bounded string and the server sets truncation flags; see configuration.md.

Overflow/retry exhaustion: check connectivity and backend limits (120 requests/min per agent per instance; 2,000 spans per run). Increase batching interval for bursts. Delivery is best effort; there is no disk spool or sampling.

After Python fork initialise a fresh tracer in each child; background threads are not inherited. Worker threads likewise need their own tracer/context handling.
