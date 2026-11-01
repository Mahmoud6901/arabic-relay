# Flow-to-review latency — 2026-11-01

## Measured stages

The selected prompt provider was Codex, GPT-6-Luna, medium effort. It remains unchanged.

A real short mixed Arabic/English inline request produced these CLI events:

- Process launched: 13 ms.
- Turn started: 849 ms.
- Final agent message: 4.100 s.
- Successful `turn.completed`: 4.116 s.
- Process exited: 5.812 s.

The old collector waited for process exit, adding 1.696 seconds after a confirmed complete turn. Flow's full-transcript paste also went through the 1.2-second incremental-typing debounce. Together these added about 2.9 seconds in this measurement, after Flow had delivered text. These measurements do not include Flow's own speech recognition latency.

## Changes

Codex JSON events are collected as they arrive. Only a successful terminal `turn.completed` permits early delivery; a partial agent message is insufficient. Errors still fail, and cancellation/deadlines still terminate the process. Pipes are drained concurrently. After completion, bounded background cleanup allows normal CLI shutdown, terminating it if it exceeds five seconds. Claude's existing collection path is unchanged.

An `insertFromPaste` event in the visible Flow capture input starts preparation immediately. Incremental input and composition retain the existing debounce. Review still waits for the validated result; nothing sends automatically.

Fixed-format local logs record terminal-turn duration and inline attempt count/duration/validation code. They do not record source text, prompts, outputs, credentials or session identifiers.

## Limits and verification

A real-child-process regression originally failed at 2.12 seconds because it waited for a simulated two-second shutdown. The new collector returned the exact completed answer before one second. Other real-process tests cover partial output followed by failure, cancellation, timeout, stderr draining and ordinary process exit. The Flow paste test failed before the fix because no request had started, and now verifies immediate preparation and review without sending.

263 Python tests passed, plus the relevant interface suites. Live requests after the change returned valid results in 13.68 seconds and 5.54 seconds; the traced 5.54-second request made exactly one model call. Online response variability remains significant. The fixed local overhead is removed; these samples do not establish a universal total-latency improvement or justify lowering the user's chosen model/effort.

The rebuilt and restarted desktop returned a validated, sendable test result in 3.91 seconds. Its logs show a 3,873 ms completed turn, a single 3,890 ms model attempt with successful validation, and a 3,891 ms API response: approximately 18 ms from completed turn to validated response. No destination received a message. This update is installed locally; no public automatic-update release was published.
