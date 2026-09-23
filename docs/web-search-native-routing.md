# Web search routing repair — 2026-09-14

High/Low/Code model rows had `params.function_calling = legacy`. In Open WebUI this runs retrieval before generation, skips native builtin tool injection, and does not expose `search_web` / `fetch_url` for iterative research. The model could therefore receive snippets without being able to request another search. A Pipe policy tested with explicitly supplied tools did not cover this UI route.

The scoped migration `scripts/configure_native_web_search.py` sets these three model rows to `native`, preserves other parameters and metadata, enables the web-search builtin category, and retains web search in default features. Local 80B is unchanged. Model identifiers and upstream routing are unchanged. User permissions and explicit per-chat search controls remain respected.

Run the migration inside `open-webui` with Python. It creates a timestamped JSON backup of the affected model rows in `/app/backend/data/` before updating them. Reload the UI to refresh model settings. An explicit per-user/chat `function_calling=legacy` override can still select the old route and should be diagnosed separately.

The Pipe's `_with_tool_execution_policy` separately supplies tool-aware capability and iterative-research instructions when native tools are present. It preserves existing system instructions and tool results and respects `tool_choice=none`.

Verification: 21 Pipe tests pass. On the real DCC Hub embedded UI, High answered that web search is available, called `search_web` for Tokyo weather, then `fetch_url` when snippets were insufficient. Yahoo Weather page text and generated forecast values were checked in the tool result UI. This verification is broader than the earlier synthetic empty-search test, but does not establish that every large research task will complete.

The same real UI chat also completed “今日のニュースを教えて”: two search_web calls followed by two fetch_url calls and a substantive summary. Verification chat: https://ai.shu-dcc.net/c/ad2f6e8e-381e-4024-bc7e-2326d73a3c98 . News facts were not independently exhaustively audited.

Japanese output policy: every Pipe request now receives a Japanese-language system instruction, regardless of existing system messages or available tools. It covers progress text and post-tool answers, while preserving requested translations, code, URLs and tool argument formats. This is instruction-based enforcement, not a deterministic language filter.
