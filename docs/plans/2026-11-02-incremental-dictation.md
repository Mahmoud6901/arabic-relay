# Incremental dictation and optimization: D → C → B → A

Status: proposed implementation plan; application code has not changed.
Date: 2 November 2026. Baseline: `9ead7a0`.
Requested order: D (processing during speech), C (vocabulary and progress), B (engine optimization),
A (comprehensive evaluation). This replaces the earlier A-first proposal.

## Decision summary

1. Start with an opt-in local Whisper experiment that processes bounded chunks while recording.
2. Implement the minimum reusable worker and job identity inside D; refine them in B.
3. Add approved vocabulary aliases and unified progress in C, including both writing modes and capture surfaces.
4. Preserve the baseline path and validate correctness during every phase; run the full comparative evaluation in A.
5. Decide default availability and release readiness from measured results. No speed or accuracy improvement is assumed.

## Scope and user-visible outcome

With the experiment enabled and local Whisper selected, recording begins normally. Relay processes available audio
while the user speaks and displays a separate provisional transcript. Stop flushes the remaining audio, finalizes
the transcript, then performs the existing Craft a prompt or Inline translation preparation. The user reviews the
finished prompt, chooses recipients, and clicks Send.

This covers the main workspace and Quick capture, Windows CPU and supported NVIDIA GPU configurations, and both
writing modes. Cohere and Wispr Flow retain their existing speech processing; C's shared progress and appropriate
text corrections also apply to their final transcripts. Existing Codex/Claude model and effort selections remain
effective. This plan does not introduce a new speech-model download or external inference provider.

## Architecture decision

```text
selected microphone stream
    ├─ existing encoded recording retained for the current review/retry lifecycle
    └─ bounded PCM capture/resampling
          → sequence-checked local recording job
          → single model-owning child process
          → overlapping whole-window Whisper inference during recording
          → provisional text + word times + audio coverage
Stop → flush → finalize or full-clip fallback
     → final transcript → existing writing-mode pipeline → review → explicit Send
```

This is incremental chunk processing around an offline model, not a claim that faster-whisper has become a
native streaming recognizer. Do not decode each MediaRecorder WebM/Opus slice as a standalone file: later slices
may depend on container headers or previous data. Reuse the microphone stream for an AudioWorklet-based PCM
path, or a proven equivalent, with tested resampling to 16 kHz mono. Validate WebView2 support in the frozen app.

Keep one worker and one inference owner. Use a versioned, length-bounded protocol with request/job IDs and
explicit payload lengths; EOF cannot delimit successive requests. Keep stdout machine-readable and drain stderr
concurrently. Model readiness, per-request failure, cancellation, and process failure are distinct events.

## D — Processing during speech

### D1. Preserve the baseline and prove transport

- Keep the existing full-clip path available. Add an experiment setting, off by default, stored through the normal
  settings mechanism. Label it local Whisper only; do not claim support for Flow/Cohere audio.
- First prove microphone-to-PCM delivery, resampling, a reusable worker request/reply round trip, and Stop/Cancel
  inside installed WebView2. Reuse an existing installed multilingual model.
- Add minimum per-stage monotonic timestamps and a baseline comparison command now. These are implementation
  diagnostics, not completion of Phase A. Preserve baseline results/settings before changing the default path.
- If native capture or worker transport cannot meet the invariants, keep the experiment disabled and report the
  concrete failure. Do not substitute a different model, runtime, or cloud upload without revising this plan.

### D2. Bound incremental inference

- Starting prototype parameters: roughly 12-second windows with 2 seconds of overlap; use pauses when practical
  and cap any window at 15 seconds. These are tunable candidates, not approved production thresholds.
- Retain complete audio coverage up to the existing five-minute recording limit. Enforce sequence numbers,
  declared lengths, sample format/rate, byte limits, and job ownership server-side. Reject duplicate/out-of-order
  chunks explicitly rather than silently duplicating or dropping audio.
- Run one inference request at a time. Bound pending work. If inference falls behind, stop partial inference and
  retain the authoritative clip for whole-clip processing after Stop; never accumulate an unlimited decode queue.
- Use multilingual transcription and the existing approved vocabulary hints. Translate only after finalization.
- Keep provisional text separate from editable final source. Tentative words may change. Never label an overlap
  hypothesis permanently stable unless the reconciliation policy supports that claim.

### D3. Finalize and recover

- On Stop, close input, flush the final samples, and wait for bounded outstanding work. Reconcile overlaps using
  absolute word timestamps and explicit coverage; plain string concatenation or broad fuzzy-text deletion is insufficient.
- Preserve repeated spoken words, punctuation, numbers and negation. Offset chunk-local timestamps to the complete
  recording timeline. Inline mode needs coherent final segment/word metadata, not only a joined string.
- Missing coverage, inconsistent timestamps, ambiguous seams, failed inference or an excessive backlog triggers
  the existing whole-clip path. Do not describe this fallback as an incremental speed win.
- A cancellation discards provisional text/audio owned by that job, cancels the worker request, and invalidates
  future results from it. Kill/restart an unresponsive child when native cancellation cannot safely stop it.
- Reuse existing review/retry and retention controls. No new audio is written to disk, uploaded, or retained in history.
  Finished recording audio follows the existing in-memory retention limits and explicit Save recording behavior.

### D exit gate

- [ ] Installed WebView2 can capture and finalize real audio with no extra microphone stream left running.
- [ ] Crafted prompts and Inline translation receive a final transcript only; no model call is made for each partial.
- [ ] Boundary fixtures cover split Arabic words, Arabic prefixes attached to English, repeated phrases, identifiers,
  numbers, negation, silence and short utterances. Known dropped/duplicated content prevents promotion.
- [ ] Stop/Cancel, main/popup ownership, delayed responses, worker failure and backlog fallback are exercised.
- [ ] Existing full-clip behavior remains selectable and has no experiment-induced regression.

## C — Approved vocabulary and clear progress

### C1. Vocabulary model and editing

- Extend the existing SQLite vocabulary design with explicit alias rules: heard form, canonical English spelling,
  enabled state, scope, and approval provenance. Upgrade existing databases transactionally without deleting learned terms.
- Learned corrections may be proposed as aliases; only explicitly approved rules perform deterministic replacement.
  Do not automatically approve every historical hint as a replacement rule.
- Provide add/edit/disable/delete controls and display what changed. Example: `ريدزاين → redesign`.
- Support global and optional project-scoped aliases. Main and popup capture snapshot an explicit vocabulary scope
  at recording start. Offer a dedicated scope selection independent of Send recipients, which may be chosen later.
  Default to global; do not infer scope from the foreground app or merge vocabularies from multiple destinations.
- Resolve exact phrase matches longest-first, with Unicode-aware boundaries and conservative handling of Arabic
  prefixes. Conflicting/ambiguous mappings need user correction; do not silently guess.

### C2. Integration with both modes

- Apply approved rules to finalized source, before optional AI spelling correction and writing-mode preparation.
  Preserve the original source and annotate replacements. Aliases supplement the user's selected AI correction policy.
- Protect already-English text, identifiers, paths, numbers, quoted/protected literals and original ordering.
  Unknown words still depend on recognition, contextual correction, or user edits. This is not neural-model training.
- Route Inline source corrections through its revision/digest/span-validation flow. Recompute slots from the
  corrected source, keep original ASR provenance available, and invalidate stale prepared results/proofs.
- Do not learn aliases from generated translations or provisional text. A prepared English draft is not a reviewed
  source correction. Source snapshots, playback and recognition retry must remain consistent.

### C3. Shared job lifecycle

Represent each capture/preparation operation with an owner surface, job ID, generation, stage, cancellation state
and timestamps. Extend the existing Inline protections instead of adding a competing job system.

```text
idle → recording → transcribing → correcting (when enabled)
     → preparing → review → sending → delivered / partial result
cancel / failure → editable recovery state, with a clear reason
```

- Display actual stage and elapsed wait, not invented percentage completion. During recording, partial transcription
  is subordinate activity of that recording job, not a second uncontrolled state.
- Old/cancelled results cannot replace newer source or prompts. A busy worker returns a clear state; there is no
  hidden unbounded queue between main and popup. Destination refresh remains independent of speech/typing.
- Preparation failure preserves the source for editing or retry. Raw Arabic/mixed text is not relabeled as a ready
  English prompt; Inline unresolved-source checks and exact-text Send validation remain enforced.
- Keep existing explicit delivery and per-destination acknowledgement behavior. Provisional, cancelled and failed
  preparations never create sent-history entries or trigger delivery.

### C exit gate

- [ ] Alias CRUD/migration, approval, Unicode boundaries, overlap, scopes and protected literals pass focused tests.
- [ ] Existing learned vocabulary and self-learning enable/disable controls survive migration.
- [ ] Both modes work with Whisper, typed/pasted text, and final Cohere/Flow text where applicable.
- [ ] Cancel/re-record/edit/model-choice races cannot display or send a stale result in either surface.
- [ ] Progress, recovery and theme/accessibility behavior are verified in the desktop app.

## B — Worker lifecycle, resources and recovery

- Refine D's minimum worker into a single reusable owner keyed by model path, model identity and device/config.
  A model/device switch waits for cancellation/retirement; it never silently starts two large model copies.
- Add readiness/prewarm status and idle unloading. Initial proposal: release after five idle minutes, configurable
  or disabled in Settings. Never unload while recording, finalizing, or running another owned inference request.
- Measure memory across repeated jobs; release job buffers immediately after their retention obligations end.
  If allocations keep growing, recycle the worker rather than claiming that retaining every allocation is optimization.
- Classify GPU errors. Preserve serial-on-GPU fallback for batched OOM where applicable; distinguish resource
  pressure from a driver/device failure. A transient OOM must not permanently disable the GPU.
- Add bounded failure memory for Auto device choice, keyed to device/driver, with a visible retry/reset control.
  Set retry/backoff policy from actual observed failures. CPU fallback must be disclosed and cannot silently change
  the selected speech model or prompt-preparation model.
- Retain parent-death monitoring, hidden child processes, bounded IPC, cancellation deadlines and clean app shutdown.

### B exit gate

- [ ] Consecutive recordings reuse the worker/model and avoid repeated import/model-load work.
- [ ] Idle release, wake/reload, model switch, app quit, child crash and stalled inference recover correctly.
- [ ] Long/repeated jobs stay within an explicitly measured memory budget; there is no unexplained upward trend.
- [ ] GPU OOM/device failure/driver-change and manual CPU selection follow distinct, tested recovery paths.
- [ ] Cohere/Flow paths and existing update-busy protections remain functional.

## A — Comprehensive evaluation and release decision

### Corpus and privacy

Begin with at least 20 licensed or consented reference clips spanning Arabic-only, English-only, Egyptian Arabic
code switching, technical names/literals, quiet/noisy speech, short utterances and longer pause-rich recordings.
Add a separately held-out set before claiming general improvement. Obtain human-reviewed references for meaning,
English terms, numbers and negation. Do not silently record the user, recruit testers, upload personal clips, or
commit private recordings/transcripts. Missing consented data is a recorded evidence gap, not a passing result.

### Compare configurations

Run the preserved baseline and candidate with identical audio, model, device, vocabulary and prompt-provider
settings. Distinguish cold start, warm model, incremental success and full-clip fallback. Randomize/interleave runs
where practical. Record sample counts and variability; repeat timing runs rather than relying on one favorable clip.
Evaluate main/popup and both writing modes. Verify CPU and a supported GPU, then obtain representative lower-end
Windows evidence before promoting the experiment broadly.

Report recording-start cost, model load, ASR, post-Stop wait, spelling correction, prompt/inline preparation,
review-ready time, cancellation latency and peak/idle memory. Show median and p95 with sample size; small-sample
tail estimates are provisional. Separate online-provider variability from local speech-engine improvements.

Use term recall, exact English/literal preservation, human semantic checks and dropped/duplicated boundary errors
alongside WER. Do not tune against held-out references or pass expected reference words into recognition hints.
Do not advertise a percentage speed improvement until repeated measurements support it.

### Release gate

- [ ] Existing Python/UI suites and new focused tests pass; frozen-app self-test and installer smoke test pass.
- [ ] No known new omissions, duplications, changed numbers/negation or protected-English mutations in acceptance
  fixtures; any quality regressions in held-out clips are reviewed explicitly before a default change.
- [ ] After-Stop median and p95 improve on the agreed target configuration, without an unacceptable cold-start,
  memory, CPU or quality regression. Quantitative targets are set from the recorded baseline before candidate tuning.
- [ ] Consent/data limitations, fallback frequency, hardware and model/provider configuration are recorded honestly.
- [ ] Review the results with the user. Decide whether to ship off by default, promote it, revise it, or retain only
  the worker/vocabulary improvements. Do not force an unsuccessful incremental design into the release.

## Implementation map

| Area | Likely files | Responsibility |
| --- | --- | --- |
| Capture/UI | `src/arabic_relay/index.html`, new packaged audio-worklet asset | PCM capture, provisional display, review, job-aware UI and opt-in setting |
| Backend | `server.py`, `speech.py`, `speech_worker.py`, focused new speech module if needed | Bounded transport, worker ownership, incremental reconciliation and fallback |
| Vocabulary | `store.py`, `translate.py`, `inline.py` | Safe migration, approved aliases, source provenance and existing text validations |
| Desktop | `desktop.py`, shutdown/update integration | Cross-window ownership, lifecycle and clean teardown |
| Verification | speech/preload/inline/vocabulary/voice/quick/responsiveness tests; benchmark runner | Boundary/race/resource tests and repeated baseline/candidate measurements |

Serve only explicit packaged assets/routes with existing Host/CSRF checks. Include new audio assets in both wheel
and PyInstaller packaging; verify MIME types and WebView2 loading. An authenticated recording ID must not grant
another surface access to a different job. Enforce protocol limits at the server and worker, not only in JavaScript.

## Work checkpoints and delivery

- [ ] D: transport/worker feasibility checkpoint, followed by incremental reconciliation and desktop verification.
- [ ] C: vocabulary/progress checkpoint with both modes and both surfaces.
- [ ] B: repeated-use/resource/recovery checkpoint.
- [ ] A: written comparative evidence and a user-reviewed release/default decision.

Commit and push verified milestones under the repository's delivery rule. Do not publish partial experiments as
successful optimizations. Update README/Arabic guide/privacy notes for actual changed behavior. Rebuild and restart
the local desktop app for native verification without discarding an existing user draft. A public version bump,
signed release and automatic-update publication follow the final release decision, not this planning commit.

The earlier day estimates were rough developer-effort estimates, not measured delivery promises. Re-estimate
after D's transport/worker checkpoint identifies actual WebView2, model-throughput and reconciliation constraints.

## Hex reference and reuse

Reviewed Hex revision: `54d1ebfc`. Relevant sources: `src/transcription/engine.rs`,
`src/transcription/engine/streaming.rs`, `src/transcription/gguf_session.rs`, `src/transcription/gpu_health.rs`,
`src/transcription/benchmark.rs`, `src/dictation/vocabulary.rs`, and `src/dictation/text_replacements.rs`.
Hex's catalog marks the inspected Whisper/Qwen Arabic-capable choices non-streaming. Its model speed strings
and source comments are not measurements on Relay's hardware or code-switched corpus.

Adapt the design to Relay's existing Python/faster-whisper stack. If substantial source is copied, retain the
applicable MIT notice and inspect component licenses separately. A Rust/Rune rewrite, OpenCode service dependency,
meetings, screen recording, automatic foreground paste and new global shortcut modes are outside this plan.
