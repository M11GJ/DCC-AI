# DCC command integration — native-first

Removed `/clear` and its implementation. Use Open WebUI's New Chat control. Removed the duplicate `/compact` prompt, compaction backend patch, disabled-check bypass and custom plan card/decision handling.

`ENABLE_CONTEXT_COMPACTION=true` enables Open WebUI's native Compact command and standard automatic compaction. Existing threshold settings are preserved. The deployed context_compaction.py SHA-256 matches the official v0.11.3 image exactly. No manual-only behavior or summary application override remains.

Plan confirmations use Open WebUI's unmodified `stage_ask_user_tool_calls` + `pause_for_tool_approval`, and its native question renderer and answer/resume workflow. No custom confirmation DOM, buttons, forms, polling panel or approval-message submission remains. Existing pending plans were backed up and staged as native questions. The retired custom /plan-decision endpoint returns 410.

Remaining extensions implement only features not supplied natively: immediate `/plan` and `/grill-me` selection, a small mode indicator, per-chat mode metadata, and tool restrictions. Plan execution requires the native question's explicit Execute choice. Free text routes to revision; cancellation never enables execution. The server reads the saved native tool result, not model-written claims of approval. Normal requests retain normal tools; restricted modes expose only ask_user. Module versioning is used for deployment cache invalidation.

Backups: /opt/dccai-backups/native-restore-20260915/ and /app/backend/data/dcc-native-restore-*.json. User messages and existing summaries were preserved. Removed obsolete patch/test files are retained in the backup directory on the server.
