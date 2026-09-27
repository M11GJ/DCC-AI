# Public error handling

`dccai_errors.py` classifies the error cause before the HTTP status or LiteLLM fallback wrapper. Public errors contain a short Japanese message plus `DCC-` correlation ID; Responses API errors retain HTTP status and JSON `error` structure with `code`, `type`, and `request_id`.

Covered boundaries: Pipe HTTP errors, retries exhausted and exceptions (streaming and non-streaming); Responses gateway upstream errors, parse failures and exceptions; Open WebUI tool exceptions in builtins/middleware and request/completion exception boundaries. Successful tool return strings and preexisting short validation messages are not arbitrarily rewritten. Genuine SearXNG empty-result responses are still a separate issue: no exception exists to classify.

Detailed error text is logged to container stderr and `/app/backend/data/dccai_errors.jsonl` (gateway: `/webui-data/dccai_errors.jsonl`, same shared volume). Correlate by request_id. Logs redact recognizable credentials and data-URL image payloads, omit request bodies and cap each detail at 32768 characters. JSONL file is created mode 0600. Never post the detailed log to users. Logs are retained; no deletion or rotation of existing logs is performed by this patch.

Image failures may be invalid/unsupported image data, not necessarily the file extension. Quota and CAPTCHA are checked before generic 429 so they are not mislabeled as congestion. Unknown errors return a generic message with a correlation ID.

Deployment: Dockerfile applies `scripts/patch_public_errors.py` to Open WebUI 0.11.3; Compose mounts the shared module in both services. Pipe content must also be synchronized into the function database. Backups: `/opt/dccai-backups/public-errors-20260914/` and image `dccai-open-webui:before-public-errors-20260914`.

Validation: 7 new error tests, 21 Pipe regressions, 5 native tool recovery tests, 9 Responses gateway tests. New tests include cause/status precedence, log correlation, secret redaction, streaming/non-streaming Pipe errors and both Responses modes retaining HTTP status without exposing upstream bodies.

Live validation after deployment: deliberately invalid image rejected by DeepSeek through all four paths (Pipe stream/non-stream, Responses stream/non-stream). All returned only Japanese guidance with DCC correlation IDs; Responses retained HTTP 400. Each ID was located in the persistent shared JSONL with the original sanitized provider error. Both public AI health and Responses health returned HTTP 200.
