# Arabic Relay — Public Release Design

Date: 2026-10-20
Status: Approved in brainstorming, awaiting spec review

## Goal

Turn Arabic Relay from a personal tool into a public, open-source Windows app that anyone can install with a
Setup.exe, with the local speech model set up for them on first launch.

## Decisions

| Topic | Decision |
|---|---|
| Audience | Arabic speakers dictating prompts for AI coding tools (T3 Code, ChatGPT, Claude) |
| Speech model | Whisper large-v3 (faster-whisper format), downloaded on first launch from Hugging Face |
| No NVIDIA GPU | CPU int8 fallback with a warning; large-v3-turbo preselected |
| Arabic → English | Codex CLI or Claude Code (whichever is installed), with user-chosen model and effort |
| Platform | Windows only for v1 (macOS later) |
| Distribution | Setup.exe on GitHub Releases (winget later) |
| License | MIT, open source |
| Packaging | One app; faster-whisper bundled; NVIDIA cuBLAS (553 MB download) only if a GPU is detected; the same exe relaunched with `--speech-worker` |
| Updates | Automatic, Ed25519-signed with an offline key |
| Code signing | v0.1 unsigned; apply to SignPath Foundation; sign from v0.2 once approved |

## Part 1 — Structure

One install folder, one program, two roles:

- `Arabic Relay.exe` opens the window, as today.
- For transcription, the app starts the same exe with `--speech-worker` in the background. This replaces borrowing
  Voice Bridge's Python and keeps today's isolation: a crashed or stuck transcription cannot freeze the window.

Code becomes a package at `src/arabic_relay/`, one job per file:

| File | Job | Today |
|---|---|---|
| `paths.py` | Data, model and settings locations; no hardcoded `E:\` paths | scattered in `app.py` |
| `locate.py` | Shared "find it in the usual places, else let the user pick it" logic | `translator_path()`, `wispr_flow_path()` |
| `models.py` | Download, verify and locate the Whisper model and NVIDIA libraries | new |
| `speech.py` | The three speech choices: local Whisper (via the worker), Cohere, Wispr Flow detection | `transcribe()`, Cohere code |
| `speech_worker.py` | Runs faster-whisper on GPU or CPU with vocabulary hints | `local_speech.py` |
| `translate.py` | Arabic → English via Codex or Claude Code; lists each tool's models and effort levels | `codex_text()` |
| `store.py` | Vocabulary, corrections, send receipts (SQLite) | `app.py` |
| `t3.py`, `desktop_chats.py` | Delivery to T3 Code and to ChatGPT/Claude desktop | mostly unchanged |
| `updates.py` | Update check, download, signature verification, install on quit | new |
| `server.py` | Local web API used by the window | `Handler` in `app.py` |
| `desktop.py` | The window | unchanged |

`index.html` and the current Monologue-based UI carry over. All existing features and the 58 existing tests carry
over (tests move to `tests/`).

### Locating outside tools and files

Rule for every outside dependency: look in the usual places first, then let the user point to it. Every saved path is
re-checked at launch; a moved or deleted target shows a clear message.

- **Wispr Flow:** standard install folders, plus the Windows installed-programs registry (finds custom install
  folders), plus a **Locate Wispr Flow…** button. Detection only drives the Open button and status line; dictation
  works without it because Flow types into the transcript box.
- **Codex / Claude Code:** PATH, npm global folder, `~/.local/bin`, the WinGet links folder, plus **Browse…**.
- **Whisper model:** Hugging Face cache (`%USERPROFILE%\.cache\huggingface\hub`), plus **Use a model I already
  have…**. Folders are validated as faster-whisper (CTranslate2) format: `model.bin`, `config.json`, tokenizer and
  vocabulary files. OpenAI `.pt` and whisper.cpp `.bin` files are refused with a message naming the expected format.
- **NVIDIA libraries:** if CUDA 12 is installed system-wide (cuBLAS under `CUDA_PATH\bin`), use it and skip that
  download. cuDNN is never needed separately: CTranslate2 ships its own.

### Speech choices

| Choice | Default | Privacy |
|---|---|---|
| Local Whisper | Yes | Audio stays on the computer |
| Wispr Flow | No | User's Wispr account; audio goes to Wispr. Not affiliated; link to the official site |
| Cohere | No | User's own API key; audio goes to Cohere. Labeled "Cloud — sends audio" |

## Part 2 — First launch and model download

**Setup screen** (one time, inside the window) shows a checklist: GPU (name, VRAM), Codex, Claude Code, Wispr Flow,
free disk space, and what will be downloaded with sizes. Actions: **Download and set up**, **Use a model I already
have…**, **Skip, I'll use Wispr Flow**.

**Download**
- Model: `Systran/faster-whisper-large-v3` (3.1 GB) or large-v3-turbo (1.6 GB) via `huggingface_hub` (already a
  faster-whisper dependency) into `%LOCALAPPDATA%\Arabic Relay\models`. Resumable and verified by the library.
- GPU libraries: only when an NVIDIA GPU is found. cuBLAS only (553 MB download, 771 MB on disk): NVIDIA's official
  wheel from PyPI, pinned by SHA-256; the app extracts `cublas64_12.dll` and `cublasLt64_12.dll` into `runtime\`.
  CTranslate2 ships its own cuDNN (measured 2026-10-20). NVIDIA files are never redistributed by us.
- Progress bar with size and speed; Cancel keeps partial files so the next attempt resumes.
- Disk space is checked before downloading.

**After download:** a 2-second test transcription. If the GPU fails to load, switch to CPU automatically and say so.
After setup the model loads with `local_files_only`; dictation makes no network calls.

**No NVIDIA GPU:** warning "No NVIDIA GPU, so transcription will run on the CPU and take a few seconds per
sentence." large-v3-turbo is preselected; large-v3 is one click away.

**Settings → Transcription:** model, path, device (GPU/CPU), **Re-download**, **Change folder**, **Delete model**,
and "Always use CPU".

## Part 3 — Translation backends

**Kept from today**
- Codex runs `codex exec` with no shell, no patching, no web search, read-only sandbox, ephemeral session, user
  config and rules ignored.
- Claude Code runs `claude -p` with no tools, no MCP servers, no settings sources, no slash commands, no saved session.
- `OPENAI_API_KEY`, `CODEX_API_KEY` and `ANTHROPIC_API_KEY` are removed from the child environment, so usage always
  goes through the user's subscription login, never an API bill.
- One translation at a time; dictation passed as JSON on stdin.

**Settings → English prompt**

```
Tool     [ Codex CLI   ▾ ]   only installed tools are selectable
Model    [ GPT-6.1-Sol ▾ ]
Effort   [ low         ▾ ]   Lower is faster. Higher handles long or messy dictation better.
```

- Codex models come live from `codex debug models` (only `visibility: list`), refreshed when Settings opens. Effort
  options are the model's `supported_reasoning_levels`; `ultra` is hidden.
- Claude Code models: Fable, Opus, Sonnet, Haiku (aliases always resolve to the newest version). Effort: low, medium,
  high, xhigh, max.
- Both lists start with **Default** (the tool's own default) and end with **Custom…** (free-text model name).
- Effort defaults to **low**, matching today.
- If `codex debug models` fails or changes format, the list falls back to Default and Custom… and never blocks.
- Passed as `-m <model>` / `-c model_reasoning_effort=<x>` (Codex) or `--model <model>` / `--effort <x>` (Claude).
- The same choice is used for Prepare English prompt and Improve English spellings.
- The line next to Prepare shows the active choice, for example "Codex · GPT-6.1-Sol · low".
- Migration: today's `prompt_model` setting (`codex:gpt-6.1-sol`) becomes `{tool: codex, model: gpt-6.1-sol,
  effort: low}`.

**Login state:** Codex via `codex login status`; Claude Code inferred from the first failed call's error text.

**Errors**

| Situation | Message |
|---|---|
| Neither tool installed | "Install Codex CLI or Claude Code to prepare English prompts. You can still type English yourself." (links to both) |
| Not signed in | "Codex isn't signed in. Open a terminal and run `codex login`." |
| Usage limit | "Your Codex usage limit was reached. Switch to Claude Code in Settings or try later." |
| Timeout (90 s) | "Translation took too long. Try a lower effort." |
| Model removed or renamed | Fall back to Default and note it once |

The Arabic transcript always stays in the box.

**Out of scope for v1:** direct API-key mode, local translation LLMs.

## Part 4 — Data, settings, privacy, repo hygiene

**Personal items that must never be committed:** `catalog.json` (T3 project names and paths; generated at runtime
into the data folder instead), `vocabulary/vocabulary.sqlite3`, `.local/relay.sqlite3`, the exes, `build/`,
`desktop/`, `.taste-skill/`, `.impeccable/`, `graphify-out/`. Hardcoded `E:\` paths in `app.py` (`VOCABULARY_DIR`,
`SPEECH_ROOT`, the Voice Bridge error message) are removed.

**Data folder:** `%LOCALAPPDATA%\Arabic Relay\` (overridable with `ARABIC_RELAY_DATA_DIR` for portable mode)

```
relay.sqlite3        settings, send history, T3 auth (DPAPI-encrypted)
vocabulary.sqlite3   learned words and corrections
cohere.bin           optional Cohere key (DPAPI)
models\              Whisper model
runtime\             NVIDIA DLLs
updates\             downloaded updates awaiting install
logs\                rotating log, 1 MB × 3; never transcripts or audio
```

Settings stay in the existing `settings` table in `relay.sqlite3` (no separate `settings.json`). The owner's existing
vocabulary in the old repo folder (`vocabulary\`) is migrated once by copying it into the data folder.

**Uninstall** removes the app, then asks "Also delete your vocabulary and downloaded models?" (default **No**).

**External connections** (listed in README and `PRIVACY.md`):
1. Codex / Claude Code — transcript text to OpenAI or Anthropic via the user's own login.
2. Cohere — optional, off by default; audio to Cohere.
3. Hugging Face / PyPI — one-time model and GPU library downloads.
4. GitHub Releases — daily update check (can be turned off).

No telemetry, analytics or crash reporting.

**T3 Code integration** stays, optional, shown only when a T3 install is detected. Users without T3 get Copy and
desktop-chat sending.

**Repo layout**

```
src/arabic_relay/   package (Part 1) + index.html, fonts/ (OFL, license files included), assets/
tests/
installer/          Inno Setup script
scripts/            sign_release.py
docs/               RELEASING.md, screenshots, specs and plans
LICENSE (MIT)  README.md  PRIVACY.md  CONTRIBUTING.md  CHANGELOG.md
```

`README.txt` becomes `README.md` written for new users: what it is, a GIF, install, requirements, privacy, building
from source.

`DESIGN.md` (a third-party Monologue style reference), the owner's `README.txt` and `PRODUCT.md`, and the old preview
screenshots are not published; they move to a git-ignored `.archive/`.

**Before the first push:** strict `.gitignore` first, then gitleaks over the repo and a grep for `E:\`, the owner's
username and T3 project names.

## Part 5 — Installer, release, updates

**Build:** PyInstaller folder mode (faster start, fewer antivirus false positives than single-file). Bundles Python,
faster-whisper, CTranslate2, PyAV, pywebview, UI and fonts. Estimated 120–150 MB; confirm in the first build.

**Installer:** Inno Setup, `ArabicRelay-Setup-<version>.exe`
- Per-user install to `%LOCALAPPDATA%\Programs\Arabic Relay`, no admin rights.
- Start menu shortcut; optional desktop shortcut.
- Runs Microsoft's WebView2 bootstrapper only when WebView2 is missing.
- Upgrades in place; asks to close the running app first.

**Release pipeline (GitHub Actions)**
- Every push and PR: tests on a Windows runner.
- Tag `v*`: tests → PyInstaller → `--self-test` → Inno Setup → silent install, self-test, silent uninstall → **draft**
  release with Setup.exe, `SHA256SUMS.txt`, notes from `CHANGELOG.md`.
- The owner runs `python scripts/sign_release.py <tag>` locally: signs the release with the offline Ed25519 key,
  uploads the signature, publishes the draft.
- Semver, starting at 0.1.0.

**Code signing:** v0.1 ships unsigned; the README explains the SmartScreen prompt and gives the checksum. Apply to
SignPath Foundation (free for open source); once approved, Authenticode signing is one more workflow step.

**Auto-update**
1. Daily check of GitHub Releases. If newer, download Setup.exe in the background into `updates\`.
2. Verify the Ed25519 signature against the public key built into the app. Once Authenticode signing exists, also
   require the same publisher as the running app. On failure, delete the file and log it.
3. Banner: "Arabic Relay 0.2.0 is ready · What's new · Restart and update".
4. Install runs Setup.exe with `/VERYSILENT /CLOSEAPPLICATIONS /RESTARTAPPLICATIONS`; the app reopens.
5. If the banner is ignored, the update installs when the app quits ("Install updates automatically when I quit", on
   by default).
- Never during recording, transcription, translation or sending.
- Settings → Updates: Automatic (default), Notify only (banner opens the release page), Off.
- The signing key never leaves the owner's computer and must be backed up (for example in a password manager). If it
  is lost, users must reinstall manually once.
- New dependency: `cryptography` (signature verification).

**Later:** winget, macOS.

## Part 6 — Error handling and testing

**Rules:** the user's dictation and drafts are never lost; every error names the next step.

| Failure | Behavior |
|---|---|
| Speech worker crashes | Restart once; then "Speech engine stopped. Restart it" with details in the log |
| GPU out of memory / CUDA error | Fall back to CPU for that recording and say so |
| Download fails | Retry (resumes); "Use a model I already have…" stays available |
| Disk full | Checked before download; message states the space needed |
| App already running | Second launch focuses the existing window |
| Port 8791 busy | Use a free port |
| Update install fails | Old version keeps working; next launch offers retry or manual download |
| Unexpected crash | Traceback to `logs\`; next launch offers **Copy diagnostics** |

**Copy diagnostics:** app, Windows, GPU, driver and tool versions, and paths with the username replaced by
`<user>`. Never transcripts or audio. Used with a GitHub issue template.

**Automated tests** (existing 58 tests kept, moved to `tests/`)
- Translation: `codex debug models` parsing (real-output fixture and garbage → fallback), effort filtering, settings
  migration.
- Paths: tool and model detection with temporary folders; model folder validation.
- Updates: valid signature passes; tampered file and wrong key are rejected; version comparison; no install while
  busy.
- Privacy guard: fails CI if the repo contains `E:\`, `.sqlite3`, `catalog.json` or key-like strings.
- Real speech on CPU in CI: Whisper `tiny` (cached) transcribes a short CC0 Arabic clip from Mozilla Common Voice.

**Build checks in CI:** `--self-test` after PyInstaller (starts the server, checks `/api/status` and the speech
worker, exits); silent install → self-test → silent uninstall after Inno Setup.

**Manual release checklist (`docs/RELEASING.md`)**
- Dictate on a real NVIDIA GPU.
- First launch on a clean machine: Windows Sandbox (Pro/Enterprise) or Microsoft's free Windows VM image (on Home).
- Update from the previous release; vocabulary survives.
- Sign the draft with the offline key, then publish.

## Constraints

- Personal data (vocabulary database, T3 auth, Cohere key, T3 catalog) never enters the repo.
- Never use an inherited `OPENAI_API_KEY` / `ANTHROPIC_API_KEY`.
- Whisper audio stays local.
- Nothing is sent to a session automatically; sending always requires the user's click.
