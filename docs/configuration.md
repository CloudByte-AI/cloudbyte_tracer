# Configuration and limits

Python init(api_key=None, base_url=None, **options); Node init({apiKey, baseUrl, ...options}). Explicit configuration overrides environment variables. One auto-capture owner per process is supported because instrumentors patch shared provider/framework modules.

| Setting | Default | Meaning |
|---|---|---|
| CLOUDBYTE_AGENT_KEY | none | Missing key becomes a no-op, one warning |
| CLOUDBYTE_BASE_URL | https://api.cloudbyte.ai | HTTP(S) URL without credentials, query or fragment |
| CLOUDBYTE_CAPTURE_CONTENT | true | false/0/no/off omits input/output |
| CLOUDBYTE_DISABLED | false | true/1/yes/on disables tracing |
| capture_content / captureContent | env | Per-tracer content control |
| service_name / serviceName | none | Adds service.name attribute |
| debug | false | Rejection counts/indexes; no payloads or keys |
| auto_capture / autoCapture | off | Integration list or true for installed integrations |
| flush_interval / flushInterval | 1 second / 1000 ms | Background batching |
| timeout | 5 seconds / 5000 ms | HTTP-attempt timeout |
| retry_delays / retryDelays | 0.5,1,2 seconds / 500,1000,2000 ms | At most four attempts |

Python timeouts are seconds; Node timeouts are milliseconds. Python flush(timeout=5) waits for queued copies to settle. Node await flush(5000) returns whether sending settled before the deadline. Settled includes dropped/rejected copies and is not a durability acknowledgment. Node shutdown(timeout=5000) is bounded. Python shutdown allows five seconds to flush and two seconds to join its daemon sender. No process signal handlers are installed; call shutdown from your application's handler.

Queue: 10,000 copies or 8,000,000 encoded bytes, dropping oldest with one warning. Oversized single copies are dropped without evicting useful copies. Batches contain at most 500 copies and 4,000,000 encoded bytes. Objects are serialized when enqueued, preserving snapshots.

Attributes are flat scalar values and trimmed to 32,768 bytes by dropping largest entries first. Names/models: 200 Unicode code points. Error messages: 4,096 code points. Non-negative integer token counts clamp to 2,147,483,647; invalid counts are omitted. The backend also limits runs to 2,000 spans and enforces retention.

## Truncation compatibility decision

The unchanged server ignores incoming input_truncated/output_truncated. It sets flags only when it truncates oversized content itself. These SDKs retain the Preview helper's bounded 70,000-byte prefix for oversized content and also send client flags. The server stores at most 65,536 encoded bytes and sets its flag correctly. This explicitly departs from the draft's 64 KB client wire cap. A server contract honoring client flags could remove this exception. No server change was made.

429 uses Retry-After whole seconds, capped at 60 seconds, default five when missing/invalid, and consumes a retry. Network/5xx failures use bounded backoff. Other 4xx are dropped. Per-span rejection diagnostics are debug-only. 401/403 clear the queue and permanently stop that tracer, printing one access-denied line. Restart after correcting access. Transport diagnostics never echo server bodies or exception strings.
