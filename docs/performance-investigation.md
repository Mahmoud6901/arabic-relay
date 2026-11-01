# Whole-app latency investigation — 2026-11-01

## Reproduced causes

1. `refresh()` set the global editing/recording lock while awaiting the destination catalog, and shortcut readiness awaited that entire refresh. A slow T3 connection could therefore disable local input for the duration of its network waits. `node tests/test_responsiveness.cjs` reproduced this with a held catalog promise: the editor was disabled. It now verifies usable editing, recording, and native shortcut readiness while the catalog is stalled, and verifies that a late catalog cannot clear another operation's busy state or replace its draft.
2. Local Whisper launched and loaded its model after Stop. Profiling measured roughly 3–4 seconds just loading large-v3. Model loading now starts asynchronously only after the microphone starts recording with Whisper. Stop claims that same process. Cancelling kills an unclaimed preload, a changed model/device discards it, and abandoned preloads expire after six minutes. Claimed workers retain the existing bounded execution, cancellation, GPU fallback, and crash handling. No audio goes to the preload endpoint.

## Measurements on this computer

Selected settings: Whisper large-v3 on GPU; Codex GPT-6-Luna, medium effort. Settings were not changed.

| Measurement | Observed time |
| --- | ---: |
| Status request | 54–64 ms |
| Full destination catalog | 1.13 s first / 194 ms repeat |
| Cached direct session catalog | 2–3 ms |
| Translator list | 1.04 s first / 308 ms repeat |
| Short mixed Arabic/English inline preparation | 5.58 s |
| Six-second Arabic clip, cold transcription round 1 | 14.78 s |
| Same clip, model loaded during six-second simulated recording | 3.78 s after Stop |
| Cold transcription round 2 | 7.18 s |
| Preloaded transcription round 2 | 2.62 s after Stop |

The preload request itself returned in 16–18 ms. Both comparisons produced identical transcript text and retained recognition evidence. These are controlled fixture measurements, not a guarantee for every recording; longer audio and online model response time remain variable. Preloading moves initialization into recording time rather than reducing the model's decoding work.

The experiment used `tests/fixtures/arabic_fleurs.wav`, with a six-second delay between `speech.prepare_worker(uuid)` and `speech.transcribe(..., inline_mode=True)`, compared against an immediate cold `speech.transcribe` call. No microphone or real destination was used.

A batched-decoding experiment was rejected because repeated speech was omitted. The deployed change retains serial inline decoding, multilingual recognition, word timestamps, vocabulary hints and beam size 5.

## Ongoing diagnostics

Local rotating logs now record only fixed API route names, elapsed milliseconds and HTTP status for slow operations and speech/prompt preparation. They do not log audio, transcripts, prompt content, session identifiers, tokens or query strings.

## Validation

259 Python tests passed, including preload reuse/cancellation/expiry/model-change tests and existing worker crash, timeout, privacy, inline, send and route tests. UI tests cover stalled discovery, native readiness, late results, Whisper-only preloading, existing providers, explicit sending and draft preservation.

The desktop executable was rebuilt, installed and restarted. Its authenticated preload endpoint returned in 30 ms; the same Arabic fixture transcribed on the GPU in 3.62 seconds after the simulated Stop. The reopened desktop showed live destinations and enabled recording/editing controls. This is a local desktop update, not a newly published automatic-update release.
