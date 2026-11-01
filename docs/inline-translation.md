# Inline translation preview

Status, 1 November 2026: usable opt-in implementation; the full implementation plan's personal-speech acceptance gate is **not complete**. Existing installations remain in Craft a prompt until the user changes Writing mode. No accuracy percentage is claimed.

## Use and disable

Above the writing cards, click **Inline translation**. This persists on this computer. Recording, the editor, source details, Copy, and Send share that card; the separate Arabic card is hidden. Choose **Craft a prompt** to restore the existing workflow.

Record and stop to transcribe and translate automatically. Wispr Flow inserts text into the English editor; Arabic phrases translate automatically after 1.2 seconds without another edit. Pasted/typed Arabic and source corrections use the same settling delay. IME composition waits until composition ends. English-only final edits stay local. There is no Translate button in this mode. Cancel stops queued/in-flight translation; a failed or cancelled attempt offers a deliberate Retry translation action without an automatic retry loop. Review/edit the final English and choose destinations. **Send is the approval action**; no separate Approve button and no automatic dispatch. Settings still controls the translation tool, model, and effort. Inline mode bypasses English spelling restoration and prompt crafting, since either can change English words.

The existing signed-in Codex/Claude Code translator processes text online. No new paid API or model download was added. Whisper audio stays local. Selecting Cohere/Flow retains that provider's existing processing policy; those providers do not expose Whisper evidence/recovery here.

Expand **Source and changes** to compare original recognition, edit the working source, protect exact terms, inspect replacements, and play retained audio. **Recognition is wrong / edit source** is available even for apparently fluent English. Known recognition warnings require acknowledgment for the current revision. New edits clear that acknowledgment.

Alternate recognition is manual, local Whisper only, for a whole recording of at most 15 seconds. Maximum two attempts and 30 seconds of combined additional wall time. Retry disables VAD and enables previous-text conditioning; compare the full alternative and explicitly choose it. Identical alternatives are not offered. Longer recordings require manual source correction in this preview; interval selection/cropping and calibrated automatic recovery remain pending. Audio is held in memory for at most 30 minutes and discarded on Clear, new recording, Send response, explicit discard, or closing the window. Unsent text is not persisted by this feature.

## Repository map and contracts

| Responsibility | Implementation | Change / verification |
| --- | --- | --- |
| Capture, mode, revisions, review, recovery | `src/arabic_relay/index.html` | Existing recorder and editor; single-card mode; stale-result rejection; `node tests/test_inline_ui.cjs` |
| Recognition adapter and process isolation | `speech.py`, `speech_worker.py` | Inline multilingual serial decoding, evidence, bounded manual retry, cancellation; Python inline/worker tests |
| Span detection, validation, reconstruction | `inline.py` | Unicode code-point offsets, immutable source slices, protected literals, strict contextual slots; Python inline tests |
| Existing online translator | `translate.py` | Existing CLI login/model/effort and safe data transport; deadline/cancel/output bound; provider lock retained |
| Local HTTP integration | `server.py` | Existing same-origin/token checks; inline prepare/cancel/validate; exact-revision send proof; route tests |
| Delivery | `t3.py` and existing direct-session adapters | Preserve exact English including outer whitespace; existing deduplication/receipt behavior retained |
| Privacy/dependencies/CI | `PRIVACY.md`, `pyproject.toml`, workflows | Disclosure, faster-whisper pin, UI regression suite |

The original ASR recognition, editable working source, and final output are separate UI values. Recognition acceptance records a separate before/after provenance entry. Translation replaces only detected Arabic slots; code-point offsets remain in Python (the UI displays strings without applying UTF-16 offsets). Internal spaces/punctuation inside a slot may be translated; all characters outside slots are reconstructed unchanged. Quoted code, fences, paths, URLs, flags, detected identifiers and explicit terms are protected. Arabic in protected literals and unfamiliar scripts require editing or switching modes; there is no silent exception.

Requests bind ID, revision, SHA-256 of exact UTF-8 source, and exact slot text. Responses must match all bindings and contain exactly one valid replacement/review item per slot. Invalid output gets at most one format retry within 90 seconds. Ambiguous/bad results preserve source and have no dispatchable output. English-only text makes zero translation calls. Per-process cancellation records are bounded to 100 IDs/30 minutes; the existing translation lock allows one provider process. Source: 12,000 code points / 64 Arabic slots; output: 16,000; provider response: 32,000. No transcript cache was added.

Send revalidates the final user-edited text and protected literals, then verifies a short-lived HMAC proof bound to exact text and revision at the HTTP delivery boundary. Destination payloads contain only the final prompt and existing routing options. Recognition/translation never invokes a selected destination.

## Environment and settings observed

- Windows, Python 3.13; desktop is pywebview/WebView2 with a loopback Python server; packaged with PyInstaller. No frontend framework or new service.
- Faster-Whisper **1.2.1**, pinned in `pyproject.toml`; existing `av>=11,<17` compatibility constraint retained. This repository has no full dependency lockfile.
- Inline local ASR: multilingual model required; `task=transcribe`, `language=None`, `multilingual=True`, serial decoder, beam 5, VAD on, word timestamps on, previous-text conditioning off, bounded existing vocabulary hotwords, no initial prompt. Audio decoded at 16 kHz. Missing evidence remains null.
- GPU uses float16; CPU fallback uses int8 and reports fallback. Existing normal mode can batch longer GPU recordings; inline mode explicitly does not. Model lifetime remains the existing isolated worker per recording, so cold loading contributes to time. No new persistent model service.
- Live local check: already-installed **large-v3**, NVIDIA RTX 4080 Laptop GPU, 12 GB, driver 616.92, CUDA/float16. Six-second Arabic FLEURS test fixture: **8.09 seconds**, one segment, ten timestamped words, no heuristic warning. This is one Arabic-only sample, not a mixed-speech benchmark.
- Translation prompt version **inline-1**. Live text checks used the existing Codex CLI, Default model, low effort. The exact resolved default model version was not exposed by the response and is not inferred. Claude adapter was covered by existing transport tests, not a new live inline capability run.

## Evidence and remaining gates

Baseline: 234 Python tests passed. After implementation: 246 passed in the full run, followed by two additional focused cancellation/Unicode tests passing (248 total test cases); all six JavaScript suites passed. The new tests include 80 seeded immutable-slice cases, repeated phrases, CRLF/tabs/emoji/presentation forms, protected literals, invalid provider schemas, missing providers, cancellation/deadlines, exact send proofs, edit/mode races, recognition acknowledgment, explicit candidate acceptance, and duplicate-send prevention. Existing real CPU Arabic/English worker tests also passed. Destination sends in these tests are mocked.

Live contextual spike: nine synthetic text fixtures, eight ready and one intentionally ambiguous `It is جامد.` requiring review. All eight ready results kept outside-slot text exact. Observed translation time 6.7–10.0 seconds; no corpus-level latency claim. Additional native desktop check: `Please راجع FeatureLayer and اتأكد من MapView.` became `Please review FeatureLayer and check MapView.` in the single card, ready for review. No session received a test prompt.

Personal recordings: **0/100**; tuning set **0/60**; held-out set **0/40**. Human reference expressions, audio alignments, semantic labels, end-to-end expression accuracy, missed-Arabic rate, p50/p95 stage timings, peak memory, and assisted success have **not been measured**. The 95% target and full AC8 gate are unmet, not lowered. Heuristic log-probability/compression warnings are not calibrated confidence or comprehensive loss detection. Fluent English may conceal missed Arabic; replay/correction remains essential.

M0 mapping and baseline, M1 deterministic core, M2 online provider integration, and the M3 preview/ASR path are implemented. M4 manual recovery is implemented for short whole recordings; the consented, annotated personal corpus, interval recovery and demonstrated automatic recovery policy remain pending. M5 existing dispatch integration/regressions are implemented; full acceptance/release certification remains gated on the measurements above. This change is an opt-in preview, not a claim that every milestone passed.

To finish that gate, collect 25 consented recordings per category (English-only, single Arabic word, Egyptian expression, mixed technical instruction), hold related re-recordings in the same stratified 60/40 split, annotate intended language/spans/meaning/literals and acceptable variants, freeze settings and held-out labels, then report every expression denominator including lost/unresolved cases. Keep private recordings outside Git. Establish hardware latency budgets before tuning; compare VAD/conditioning/model choices on tuning data only. Add automatic interval recovery only for observed failure categories, with a separately tested alignment policy. No user recordings were collected or uploaded for this task.

## Setup, verification, and manual demo

From the repository in PowerShell:

```powershell
.venv\Scripts\python.exe -m pip install -e .
.venv\Scripts\python.exe -m arabic_relay
.venv\Scripts\python.exe -m unittest discover -s tests -v
node tests/test_voice.cjs
node tests/test_history_ui.cjs
node tests/test_setup.cjs
node tests/test_translation_ui.cjs
node tests/test_ship_ui.cjs
node tests/test_inline_ui.cjs
.venv\Scripts\python.exe -m PyInstaller --noconfirm installer/ArabicRelay.spec
```

Choose an already-downloaded multilingual model in Speech settings and an installed, signed-in Codex or Claude Code in Settings. The existing model setup is still available; selecting inline mode does not download anything. Local Whisper works offline once installed; online translation still needs the chosen provider connection. English-only pass-through works without it.

1. English only: enter `Check FeatureLayer and MapView.` → automatic processing → exact same output, no translator call.
2. Idiom: enter `I was talking to my manager, but he كان بيتهرب من المسؤولية, and I didn't know what to do.` → automatic processing → the Arabic phrase alone should convey avoiding responsibility. Review meaning before Send.
3. Technical: enter `راجع src/app/map.ts and getUserById with --dry-run.` → automatic processing → inspect the three unchanged literals; Copy and Send use the same English text.
4. Recognition mistake: enter `I need to arrange the code.` → Recognition is wrong → change working source to `I need to أراجع الكود.` → automatic processing → review `I need to review the code.` If audio is retained, replay or compare a short alternate recognition before correcting. No step sends until you click Send with a destination selected.
