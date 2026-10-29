# Direct desktop session connections

Research date: 2026-10-29. Scope: a standalone Windows Arabic Relay application that lists existing sessions, preserves their identities, and sends to selected sessions without a browser extension. This document records official-interface research; it does not claim a working installed integration or a successful live send.

## Findings

| Surface | Documented discovery route | Documented continuation route | Remaining verification |
| --- | --- | --- | --- |
| Codex sessions in ChatGPT Desktop | Codex app-server `thread/list` / `thread/read` | `thread/resume` then `turn/start`; newer CLI also documents `codex queue` | Installed version, same session store and live daemon, desktop ownership, and UI synchronization |
| ChatGPT Chat / Work | Assistant-owned desktop tools exist in the current Codex conversation, but no standalone Relay-facing catalog API was established by this research | MCP App UI can message its active host conversation or start a new one | Automatic enumeration and addressing an arbitrary existing chat remain unverified |
| Claude Code local sessions | Agent SDK `list_sessions()` / `listSessions()` reads local transcripts | Resume an exact SDK/CLI session ID | Mapping desktop wrapper IDs to transcript IDs; concurrent desktop ownership and live UI updates |
| Claude Desktop regular chats | No official desktop-wide session catalog interface established here | Desktop extensions expose tools *to* Claude; they do not document arbitrary host-chat targeting | Both automatic enumeration and exact-session delivery need another verified interface |
| Claude Cowork | Enterprise Compliance API can list/read eligible session records | Compliance session endpoints are read-only | Does not provide a personal desktop send connection |

These are boundaries of the interfaces examined, not proof that no other integration can exist.

## Read-only results on the installed Windows apps

The following probes ran on 2026-10-29. Counts are observations, not coverage guarantees. No session titles, transcripts, account IDs, session IDs, or credentials are included in this report.

- **Codex standalone discovery succeeded.** Started the installed CLI's `app-server --stdio`, sent `initialize`, `initialized`, then `thread/list` with `limit: 100`, `sourceKinds: ["appServer", "cli", "vscode", "exec"]`, `sortKey: "updated_at"`, and `useStateDbOnly: true`. It returned 100 records and a next-page cursor. This research conversation's existing desktop thread ID was among the results. The page contained 89 `vscode` and 11 `exec` source values: source alone is not a reliable desktop-only filter. Returned fields included identity, name, project, status, model, effort, and direct-input capability. The probe terminated its own subprocess; it did not resume or send to a thread. This proves shared-store discovery, not attachment to the desktop's live session owner.
- **Claude Desktop 4.29.0 Code metadata is usable for discovery.** Under `%APPDATA%/Claude/claude-code-sessions/<account>/<organization>/`, 26 metadata records were found. Of those, 24 had a title, Desktop `sessionId`, and `cliSessionId`; four were archived. Twenty-five referenced a matching transcript under `~/.claude/projects/`. The adapter must handle missing titles/IDs and account boundaries. These files contain other sensitive fields; only the identity/title/status allowlist should enter Relay's catalog.
- **ChatGPT's bundled app-tools server is not a context-free API.** The installed `codex-app-tools` MCP server initialized successfully and listed 58 tools, including `list_threads`, using its inherited app-provided pipe environment. Calling `list_threads` without executor metadata returned JSON-RPC `-32602`: `Codex app tools require thread metadata from the executor.` No metadata was forged, app identity impersonated, or additional access granted. The bundled `server.mjs` requires an app-provided pipe and caller context. A successful in-conversation tool call must not be presented as proof of a standalone integration.
- **Claude's native accessibility surface exposes navigation.** A read-only inspection identified an existing Code document by its `/epitaxy/local_...` address. Switching to Claude mode exposed pinned and recent regular-session titles as buttons, including Search and View all. The row accessibility IDs were UI-instance identifiers, not conversation IDs. An attempted off-screen row selection was rejected before input; no regular chat's stable identity or send path was verified. This is evidence for a UI adapter experiment, not a complete catalog API. No prompt was typed or sent.

Local source references inspected: installed Claude `resources/app.asar` metadata/session-tool implementations, installed ChatGPT `resources/app.asar`, and the bundled `codex-app-tools/server.mjs`. These are implementation evidence for the installed builds, not stable public contracts. No application bundles were modified.

### What the next implementation experiment should prove

For coding sessions, first build a read-only catalog using the verified Codex protocol and Claude identity mapping. Then test Claude's opt-in inbox hook and Codex's live-owner submission against dedicated test sessions. Keep transport availability separate from history availability; an archived or stopped session is not necessarily reachable.

For regular chats, there are two distinct experiments: an assistant-hosted Relay connector that uses genuine app session tools, or a Windows accessibility adapter that navigates the desktop app and verifies the selected conversation. The former needs an active assistant and consumes usage; it is not a standalone host API. The latter can disturb foreground navigation, must preserve drafts, must disambiguate repeated names, and must establish stable identity after navigation. Claude's title listing is verified, but exact selection/delivery is not. ChatGPT native UI automation was not tested: the available computer-use skill explicitly excludes that app UI. That is a limitation of this investigation environment, not evidence that the product cannot technically be automated.

Do not enable a regular-chat Send button until one of these experiments proves exact-session targeting and duplicate-safe submission. No browser extension is required by the proposed coding adapters or native UI experiment.

## Codex: a concrete native protocol to test

The official app-server protocol provides stored-thread listing, metadata reads, pagination, and exact-ID resume. Include `appServer` explicitly in `sourceKinds`: omitted or empty source filters default to `cli` and `vscode`. `useStateDbOnly: true` avoids the listing operation scanning and repairing transcript metadata. For a discovery probe, initialize a local client, call `thread/list` and `thread/read`, and stop without resuming or starting a turn. Thread names can be hydrated from persisted metadata. [App-server reference](https://learn.chatgpt.com/docs/app-server).

The current CLI reference additionally documents `codex queue --thread <UUID> --message ...`, shared-server `codex agents`, and remote transport options. This is promising for addressing a running coding session, but documentation availability must be checked against the installed CLI. Do not substitute a newly launched independent runtime for the desktop's live owner without verifying concurrency semantics. [Developer commands](https://learn.chatgpt.com/docs/developer-commands).

Codex history must not be presented as the complete ChatGPT history: OpenAI explicitly describes Codex history as separate from Chat/Work history in the unified desktop app. [Desktop migration guide](https://help.openai.com/en/articles/20001276-moving-to-the-new-chatgpt-desktop-app).

## ChatGPT plugins: useful, but not an account-wide picker

OpenAI's published MCP Extensions protocol supports a plugin UI in a desktop sidebar or conversation panel. Its `ui/message` extension accepts only `target: "active"` or `target: "new"`; `send: false` can prepare a draft. A global entrypoint uses its own host thread for active-thread operations. The examined specification contains no conversation enumeration or arbitrary conversation-ID target for this message method. A native Relay plugin could therefore serve a host conversation, but that alone does not meet automatic selection of any existing session from the standalone Relay window. [OpenAI MCP Extensions specification](https://github.com/openai/mcp-extensions/blob/main/docs/spec.md#uimessage-extensions).

The assistant's `mcp__codex_app__list_threads` and `send_message_to_thread` tools are a separate runtime capability. Their availability to this research session is not evidence that a standalone executable can invoke them. A viable adapter needs an independently accessible, authorized transport—not a copied tool name or a snapshot exported once by the assistant.

## Claude Code: supported transcript discovery

Anthropic documents `list_sessions()` / `listSessions()` and message readers as local file operations over the Claude Code transcript store. Its session-browser cookbook uses these functions to build a sidebar and notes that they see CLI-written transcripts under `~/.claude/projects/`; no running agent is needed for reading. The SDK session guide documents resuming an exact stored session ID rather than the most recent session. This gives Relay a legitimate starting point for local coding-session discovery. It does not by itself establish that every Desktop, Cowork, or regular-chat identifier belongs to that store. [Session-browser cookbook](https://platform.claude.com/cookbook/claude-agent-sdk-05-building-a-session-browser), [SDK session guide](https://code.claude.com/docs/en/agent-sdk/sessions).

Claude Desktop's documentation also describes local-session Remote Control and cross-surface navigation. Remote Control makes connected local Code sessions visible on other signed-in devices; it is not documented there as a general third-party API for enumerating all desktop chats. [Claude Desktop reference](https://code.claude.com/docs/en/desktop#control-which-sessions-appear-on-your-other-devices).

The same Desktop reference documents an assistant-mediated route: Claude can list, read, and message other Desktop-owned Code sessions (local, SSH, WSL). It excludes cloud, terminal, and VS Code sessions from this particular surface, defaults to 20 recent sessions, and omits archived sessions unless requested. A local Relay MCP bridge could ask a host assistant to use this capability. That would require a running assistant and model usage, and would not establish regular-chat coverage. [Work across sessions](https://code.claude.com/docs/en/desktop#work-across-sessions).

### Native Claude Code inbox: a second concrete path

Anthropic also documents scripts and hooks posting to the running session's inbox. On native Windows this is a named pipe. `CLAUDE_CODE_MESSAGING_SOCKET` and a per-session `CLAUDE_CODE_MESSAGING_TOKEN` are exported to hooks, including `SessionStart`; an authenticated first line is required on Windows. Incoming messages still pass the receiving session's inbound controls, which can accept, hold, or refuse them. This is a promising candidate for a Relay session-registration hook: a newly registered Code session could provide its stable identity and a scoped connection to Relay instead of Relay extracting application credentials. The complete message-frame schema, acknowledgment semantics, lifecycle, and Desktop behavior still need a controlled test. The documented inbox is a Claude Code capability, not a regular Claude Chat catalog. [Cross-session messaging and inbox sockets](https://code.claude.com/docs/en/cross-session-messaging#the-sessions-inbox-socket).

Claude Code Channels are another documented push interface: an MCP server sends events to its running session. They require channel enablement and eligible authentication; docs describe terminal startup flags. Desktop availability for existing sessions is not established here. [Channels](https://code.claude.com/docs/en/channels).

## Approaches that do not establish the requested connection

- Claude Desktop MCP extensions install local tools for conversations to call. The setup documentation does not grant those servers a host conversation-list API. [Claude local MCP setup](https://support.claude.com/en/articles/10949351-getting-started-with-local-mcp-servers-on-claude-desktop).
- Claude's Compliance API can read eligible Enterprise Code/Cowork session metadata and transcripts, requires compliance authorization, and explicitly makes these endpoint families read-only. It cannot supply the required send operation. [Compliance session API](https://platform.claude.com/docs/en/manage-claude/compliance-sessions).
- A title-only cache or UI list is insufficient for exact delivery, especially when names repeat. A candidate must expose stable session identity, a fresh destination, and a verifiable send result.
- A newly created API conversation, a transcript fork, or starting a new desktop chat is not continuation of the user's selected existing desktop session.

## Acceptance evidence needed before implementing the picker

Proposed coding-session architecture: keep two adapters behind Relay's catalog interface. The Codex adapter uses the installed app-server protocol for metadata and a verified live-session transport for submission. The Claude adapter joins Desktop metadata to its CLI transcript ID, then accepts opt-in registration from a session-start helper for live inbox availability. Store the Desktop `local_...` wrapper ID separately from the CLI session UUID; never infer that one can be passed where the other is required. Registration expires when its process/session disappears. A selection can be displayed before a send transport is ready, but must be clearly unavailable for sending. Store scoped registration secrets privately, never in the exported catalog or Git.

For regular ChatGPT/Claude chats, keep the adapter unimplemented until a real transport is demonstrated. Do not substitute a Code session, a browser catalog, assistant-context impersonation, or an exported snapshot and call it direct automatic integration. An assistant-hosted bridge is a separate possible design and must be labeled as agent-mediated, with its running-session and usage requirements.

1. A read-only standalone probe returns IDs and titles for sessions known to belong to each requested desktop surface.
2. Refresh handles renames, deleted/archived sessions, and account changes without retaining stale selectable targets.
3. A test session can be addressed by stable ID and opened or prepared without altering another conversation or an existing draft.
4. A separately authorized test send appears in that same desktop session. Check running-session behavior and duplicate suppression.
5. The adapter distinguishes unavailable sessions from discoverable-but-not-sendable sessions. Do not label a partial catalog as all chats.

No credentials were extracted, no browser extension was installed, and no test messages were sent during this documentation research.
