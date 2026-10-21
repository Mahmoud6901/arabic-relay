# Plan 1 of 4 — Foundation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Turn the flat personal project into a public-safe Python package: no hardcoded personal paths, one per-user
data folder, local speech that no longer borrows Voice Bridge's Python, and `app.py` split into the spec's modules.

**Architecture:** Code moves to `src/arabic_relay/` (an editable-installed package). Static files (`index.html`,
`fonts/`, `assets/`) live inside the package so one `paths.ROOT` serves them in development and, later, in the frozen
build. Every module reads locations at call time through `paths.DATA` / `paths.ROOT`, so tests patch one place.
Behavior visible to the user does not change, except the speech model folder now comes from a setting or the
Hugging Face cache.

**Tech Stack:** Python 3.13, stdlib `unittest`, sqlite3, faster-whisper, pywebview, Node (for `test_voice.cjs`).

**Spec:** `docs/superpowers/specs/2026-10-20-public-release-design.md` (Part 1, and the data-folder and privacy parts
of Part 4). Later plans: 2 Speech (worker in the exe, model download, setup screen), 3 Translation (model and effort
dropdowns, errors), 4 Ship (installer, CI, auto-update, docs, owner switch-over).

## Global Constraints

- Data folder: `%LOCALAPPDATA%\Arabic Relay\`, overridable with `ARABIC_RELAY_DATA_DIR`.
- Never pass `OPENAI_API_KEY`, `CODEX_API_KEY` or `ANTHROPIC_API_KEY` to child processes (keep the existing `env.pop` lines).
- Whisper audio stays local; nothing is sent to a session without the user's click.
- No hardcoded drive paths (`E:\…`, `C:\Users\…`) in tracked files outside `docs/superpowers/`.
- Never commit: `*.sqlite3`, `catalog.json`, `*.exe`, `*.bin`, `vocabulary/`, `.local/`, preview screenshots, `DESIGN.md`
  (a third-party style reference) or the owner's `README.txt` / `PRODUCT.md`.
- **No commits until Task 3**: personal paths exist in the code until Task 3 removes them, and this git history will
  become public. Tasks 1 and 2 leave their work uncommitted; Task 3 commits all three.
- Do not rebuild or replace the installed `Arabic Relay.exe`. The owner keeps using it until Plan 4.
- Development runs use `ARABIC_RELAY_DATA_DIR=.local` and port 8788 so they never touch the installed app's data.
- Test commands (from the repo root): `.venv/Scripts/python -m unittest discover -s tests` and `node tests/test_voice.cjs`.

## Review Focus

1. A model folder whose path contains Arabic letters or spaces (`D:\نماذج\large v3`) must validate and be passed to the
   worker unchanged — Task 3 `test_arabic_and_space_path_validates`.
2. A saved model folder that was moved or deleted since the last launch must report "not configured", not crash —
   Task 3 `test_deleted_saved_folder_falls_back`.
3. A user who picks the Hugging Face repo folder (`models--Systran--faster-whisper-large-v3`) instead of the snapshot
   inside it expects it to work — Task 3 `test_repo_folder_resolves_to_snapshot`.
4. A Hugging Face cache moved with `HF_HUB_CACHE` or `HF_HOME` (the "installed somewhere else" case) must still be
   found — Task 3 `test_hf_hub_cache_env_is_honored`.
5. First run with no saved T3 catalog snapshot must show desktop chats only, not raise — Task 2
   `test_offline_catalog_without_snapshot_is_empty`.

---

### Task 1: Package layout and development environment

**Files:**
- Create: `pyproject.toml`, `src/arabic_relay/__init__.py` (empty), `src/arabic_relay/__main__.py`
- Move: `app.py`, `desktop.py`, `desktop_chats.py`, `index.html` → `src/arabic_relay/`;
  `local_speech.py` → `src/arabic_relay/speech_worker.py`; `fonts/` → `src/arabic_relay/fonts/`;
  `assets/imgs/icon.svg`, `assets/imgs/relay.ico` → `src/arabic_relay/assets/`; `test_*.py`, `test_voice.cjs` → `tests/`
- Move to `.archive/` (git-ignored): `DESIGN.md`, `PRODUCT.md`, `README.txt`, every other file in `assets/imgs/`,
  `Arabic Relay.spec`, `Build-desktop.cmd`, `catalog.json`
- Modify: `.gitignore`, `Start.cmd`

**Interfaces:**
- Produces: package `arabic_relay`; `python -m arabic_relay` opens the desktop window, `python -m arabic_relay --browser`
  runs the browser server (today's `app.py --open`). Tests import `from arabic_relay import app` (and `desktop`,
  `desktop_chats`).

- [ ] **Step 1: Record the baseline** — Run `python -m unittest test_app test_cohere test_connection test_desktop test_desktop_chats test_direct_send test_speech test_translation test_vocabulary` and `node test_voice.cjs` in the old layout. Expected: `Ran 59 tests … OK` and the voice script's success line.

- [ ] **Step 2: Move the files** listed above (plain moves; `git mv` is not available because nothing is tracked yet).

- [ ] **Step 3: Write `pyproject.toml`** — setuptools build, `name = "arabic-relay"`, `version = "0.1.0"`,
  `requires-python = ">=3.12"`, `dependencies = ["faster-whisper>=1.2", "pywebview>=5"]`,
  `[project.optional-dependencies] gpu = ["nvidia-cublas-cu12", "nvidia-cudnn-cu12==9.*"]`, src layout,
  `[tool.setuptools.package-data] arabic_relay = ["index.html", "fonts/*", "assets/*"]`.

- [ ] **Step 4: Fix imports and static paths**
  - `app.py`: `import desktop_chats` → `from . import desktop_chats`; `/icon.svg` serves `ROOT / 'assets/icon.svg'`.
  - `desktop.py`: `import app` → `from . import app`; icon `app.ROOT / 'assets/relay.ico'`.
  - Move the `if __name__ == '__main__':` block of `app.py` into `app.main(open_browser: bool) -> None`.
  - `__main__.py`: `--browser` → `app.main(open_browser=True)`; otherwise run `desktop`'s current `__main__` block
    (moved into `desktop.main() -> None`).
  - `app.py` keeps launching `ROOT / 'speech_worker.py'` (renamed file).
  - Tests: `import app` → `from arabic_relay import app` (same for `desktop`, `desktop_chats as bridge`);
    `test_voice.cjs` reads `path.join(__dirname, '..', 'src', 'arabic_relay', 'index.html')`.

- [ ] **Step 5: Update `.gitignore`** — add `.archive/`, `.venv/`, `*.egg-info/`.
  `Start.cmd` becomes: `cd /d "%~dp0"`, `set ARABIC_RELAY_DATA_DIR=%~dp0.local`,
  `.venv\Scripts\python -m arabic_relay --browser`, `pause`.

- [ ] **Step 6: Create the dev environment**

Run: `python -m venv .venv && .venv/Scripts/python -m pip install -e ".[gpu]"`
Expected: ends with `Successfully installed … arabic-relay-0.1.0 …`

- [ ] **Step 7: Run the suite**

Run: `.venv/Scripts/python -m unittest discover -s tests` and `node tests/test_voice.cjs`
Expected: `Ran 59 tests … OK`; voice script success line. **Do not commit** (see Global Constraints).

---

### Task 2: One data folder (`paths.py`) and the runtime catalog snapshot

**Files:**
- Create: `src/arabic_relay/paths.py`, `tests/test_paths.py`
- Modify: `src/arabic_relay/app.py` (replace `LOCAL`, `VOCABULARY_DIR`, the `catalog.json` read), `src/arabic_relay/desktop.py`,
  tests that patch `app.LOCAL` / `app.VOCABULARY_DIR` / assign `app.LOCAL`

**Interfaces:**
- Produces: `paths.ROOT: Path` (package folder), `paths.DATA: Path` (env `ARABIC_RELAY_DATA_DIR`, else
  `%LOCALAPPDATA%\Arabic Relay`; created at import). `app.catalog_response() -> dict` — the body of
  `GET /api/catalog` (`projects`, `live`, `desktopApps`, optional `t3Error`), extracted so tests call it directly. All code reads `paths.DATA` at call time — never
  `from .paths import DATA`. Vocabulary database: `paths.DATA / 'vocabulary.sqlite3'` (attached as `learned`, as today).
  T3 snapshot: `paths.DATA / 'catalog.json'`.

- [ ] **Step 1: Write the failing tests** in `tests/test_paths.py`

```python
def test_env_override_creates_folder(self):
    target = Path(self.temp.name) / 'nested' / 'data'
    with patch.dict(os.environ, {'ARABIC_RELAY_DATA_DIR': str(target)}):
        module = importlib.reload(paths)
    self.assertEqual(module.DATA, target)
    self.assertTrue(target.is_dir())

def test_default_is_localappdata(self):
    env = {'LOCALAPPDATA': self.temp.name}
    with patch.dict(os.environ, env, clear=True):
        module = importlib.reload(paths)
    self.assertEqual(module.DATA, Path(self.temp.name) / 'Arabic Relay')

def test_vocabulary_lives_in_data_folder(self):
    with patch.object(paths, 'DATA', Path(self.temp.name)):
        app.learn_vocabulary('React deadline')
    self.assertTrue((Path(self.temp.name) / 'vocabulary.sqlite3').is_file())

def test_offline_catalog_without_snapshot_is_empty(self):
    with patch.object(paths, 'DATA', Path(self.temp.name)), patch.object(app, 'load_auth', return_value=None):
        body = app.catalog_response()
    self.assertFalse(body['live'])
    self.assertTrue(all(p['id'].startswith('desktop:') for p in body['projects']))

def test_live_catalog_writes_snapshot(self):
    live = [{'id': 'p1', 'title': 'Demo', 'workspaceRoot': 'X', 'threads': []}]
    with patch.object(paths, 'DATA', Path(self.temp.name)), patch.object(app, 'load_auth', return_value={'a': 1}), \
         patch.object(app, 'catalog', return_value=live):
        app.catalog_response()
    self.assertEqual(json.loads((Path(self.temp.name) / 'catalog.json').read_text(encoding='utf-8')), live)
```

`setUp` creates `self.temp = tempfile.TemporaryDirectory()`; `tearDown` reloads `paths` with the original environment.

- [ ] **Step 2: Run them** — `.venv/Scripts/python -m unittest tests.test_paths -v`. Expected: FAIL (`No module named 'arabic_relay.paths'`).

- [ ] **Step 3: Implement `paths.py`** (two module constants as above) and wire it in:
  - `app.py`: delete `LOCAL`, `LOCAL.mkdir`, `VOCABULARY_DIR`; every use becomes `paths.DATA`; the vocabulary attach
    path becomes `paths.DATA / 'vocabulary.sqlite3'` (drop the separate folder `mkdir`).
  - Extract the `/api/catalog` branch into `catalog_response()`; on live success write the T3 part of the answer to `paths.DATA / 'catalog.json'` (write to
    `catalog.tmp`, then `replace`); offline, read it if present, else `[]`.
  - `desktop.py`: drop the `DATA` constant and the `ARABIC_RELAY_DATA_DIR` assignment; use `paths.DATA` for
    `NativeWebView` and `desktop.log`. Keep `ARABIC_RELAY_PORT='8791'` and `ARABIC_RELAY_NATIVE='1'`.
  - Tests: `patch.object(app, 'LOCAL', x)` / `app.LOCAL = x` → `patch.object(paths, 'DATA', x)`;
    drop `VOCABULARY_DIR` patches (the vocabulary now follows `paths.DATA`).

- [ ] **Step 4: Run the suite** — Expected: `Ran 64 tests … OK`; voice script passes. **Do not commit.**

---

### Task 3: Local speech without Voice Bridge (`locate.py`) and the privacy guard — first commit

**Files:**
- Create: `src/arabic_relay/locate.py`, `tests/test_locate.py`, `tests/test_privacy.py`
- Modify: `src/arabic_relay/app.py`, `src/arabic_relay/desktop.py`, `src/arabic_relay/index.html`, `tests/test_speech.py`,
  tests patching `app.translator_path` / `app.claude_path` / `app.wispr_flow_path`

**Interfaces:**
- Consumes: `paths.DATA`, `paths.ROOT`.
- Produces in `locate.py`:
  - `validate_model_folder(folder: str | Path) -> Path` — returns the usable folder or raises `ValueError` with a
    user-facing message.
  - `whisper_model(saved: str | None) -> Path | None` — the saved folder if valid, else the newest valid snapshot of
    `models--Systran--faster-whisper-large-v3` in the Hugging Face cache, else `None`.
  - Moved unchanged from `app.py`: `translator_path()`, `claude_path()`, `wispr_flow_path()`, `external_runtime()`.
    `app.py` calls them as `locate.x()` so tests patch `locate`.
- Produces in `app.py`: `speech_model_path() -> Path | None` (reads setting `whisper_model_dir`, calls
  `locate.whisper_model`); `set_whisper_model(folder: str) -> dict` returning
  `{'speechConfigured': True, 'modelPath': str}`; route `POST /api/whisper-model` with body `{"path": str}`;
  `/api/status` gains `modelPath: str | None`.
- Produces in `desktop.py`: `DesktopApi.pick_folder(token: str) -> str | None` (CSRF-checked, same as `open_sign_in`).

- [ ] **Step 1: Write the failing tests** in `tests/test_locate.py`. A helper `make_model(folder)` writes empty
  `model.bin`, `config.json`, `tokenizer.json`, `vocabulary.json`.

```python
def test_valid_folder(self):
    self.assertEqual(locate.validate_model_folder(make_model(self.root / 'm')), self.root / 'm')

def test_arabic_and_space_path_validates(self):
    folder = make_model(self.root / 'نماذج' / 'large v3')
    self.assertEqual(locate.validate_model_folder(str(folder)), folder)

def test_missing_files_are_rejected(self):
    (self.root / 'empty').mkdir()
    with self.assertRaisesRegex(ValueError, 'faster-whisper'):
        locate.validate_model_folder(self.root / 'empty')

def test_openai_pt_model_is_rejected_by_name(self):
    folder = self.root / 'pt'; folder.mkdir(); (folder / 'large-v3.pt').write_bytes(b'')
    with self.assertRaisesRegex(ValueError, r'\.pt'):
        locate.validate_model_folder(folder)

def test_whisper_cpp_model_is_rejected_by_name(self):
    folder = self.root / 'cpp'; folder.mkdir(); (folder / 'ggml-large-v3.bin').write_bytes(b'')
    with self.assertRaisesRegex(ValueError, 'whisper.cpp'):
        locate.validate_model_folder(folder)

def test_repo_folder_resolves_to_snapshot(self):
    repo = self.root / 'models--Systran--faster-whisper-large-v3'
    snapshot = make_model(repo / 'snapshots' / 'abc123')
    self.assertEqual(locate.validate_model_folder(repo), snapshot)

def test_deleted_saved_folder_falls_back(self):
    with patch.dict(os.environ, {'HF_HUB_CACHE': str(self.root / 'nocache')}):
        self.assertIsNone(locate.whisper_model(str(self.root / 'gone')))

def test_hf_hub_cache_env_is_honored(self):
    cache = self.root / 'elsewhere'
    snapshot = make_model(cache / 'models--Systran--faster-whisper-large-v3' / 'snapshots' / 'x')
    with patch.dict(os.environ, {'HF_HUB_CACHE': str(cache)}):
        self.assertEqual(locate.whisper_model(None), snapshot)

def test_hf_home_env_is_honored(self):
    snapshot = make_model(self.root / 'hfhome' / 'hub' / 'models--Systran--faster-whisper-large-v3' / 'snapshots' / 'y')
    with patch.dict(os.environ, {'HF_HOME': str(self.root / 'hfhome')}):
        os.environ.pop('HF_HUB_CACHE', None)
        self.assertEqual(locate.whisper_model(None), snapshot)
```

In `tests/test_speech.py`, replace the `SPEECH_ROOT` / `SPEECH_PYTHON` tests with:

```python
def test_worker_runs_with_this_interpreter(self):
    # existing Popen mock pattern; assert on the captured command
    self.assertEqual(command[0], sys.executable)
    self.assertEqual(Path(command[3]).name, 'speech_worker.py')

def test_saved_model_setting_round_trips(self):
    folder = make_model(Path(self.temp.name) / 'm')
    with patch.object(paths, 'DATA', Path(self.temp.name)):
        self.assertEqual(app.set_whisper_model(str(folder))['modelPath'], str(folder))
        self.assertEqual(app.speech_model_path(), folder)
```

`tests/test_privacy.py` — `test_no_personal_data_in_publishable_files`: list files with
`git ls-files --cached --others --exclude-standard`, skip `docs/superpowers/` and this test file, and fail listing
every offender for:
- file names matching `*.sqlite3`, `catalog.json`, `*.exe`, `*.bin`;
- text content (skip `.ttf .ico .png .jpg`) matching `(?<![A-Za-z0-9])[A-Z]:[\\/][A-Za-z]` (drive paths),
  the owner's projects folder name, `sk-[A-Za-z0-9_-]{20,}`, or `-----BEGIN [A-Z ]*PRIVATE KEY-----`.

- [ ] **Step 2: Run them** — `.venv/Scripts/python -m unittest tests.test_locate tests.test_speech tests.test_privacy -v`.
  Expected: FAIL (`No module named 'arabic_relay.locate'`; privacy test lists `app.py` and `index.html`).

- [ ] **Step 3: Implement `locate.py`**
  - Validation order: resolve a `snapshots/` child (newest valid snapshot by mtime) → refuse `*.pt` → refuse
    `ggml*.bin` → require `model.bin`, `config.json`, `tokenizer.json` and `vocabulary.json` or `vocabulary.txt`.
  - Messages (exact):
    - missing files: "This folder isn't a faster-whisper model. Choose the folder that contains model.bin and config.json."
    - `.pt`: "This is an OpenAI Whisper .pt model. Arabic Relay needs the faster-whisper version (Systran/faster-whisper-large-v3)."
    - ggml: "This is a whisper.cpp model. Arabic Relay needs the faster-whisper version (Systran/faster-whisper-large-v3)."
  - Hugging Face cache root: `HF_HUB_CACHE`, else `HF_HOME/hub`, else `~/.cache/huggingface/hub`.
  - Move the four existing helpers in unchanged.

- [ ] **Step 4: Rewire speech in `app.py`**
  - Delete `SPEECH_ROOT`, `SPEECH_PYTHON` and the `cache.json`-based `speech_model_path`.
  - `speech_ready()` = `speech_model_path() is not None`.
  - The worker command is `[sys.executable, '-I', str(paths.ROOT / 'speech_worker.py'), str(os.getpid()), str(folder)]`.
  - The no-model error becomes: "Choose a Whisper model folder in Speech details, then click Refresh."
  - `index.html`:
    - Replace the `#speechRuntime` text with "Speech runs on this computer with faster-whisper. Model folder:" plus a
      `<code id="modelPath">` filled from `status.modelPath` (or "not set").
    - Add a "Use a model I already have…" button (shown only when `nativeDesktop`). It calls
      `window.pywebview.api.pick_folder(csrf)`, then `POST /api/whisper-model`, then `refresh()`. It shows API errors
      with the existing `notice()`.
  - `desktop.py`: `app.external_runtime` → `locate.external_runtime`; implement `pick_folder` using `webview.windows[0].create_file_dialog(webview.FOLDER_DIALOG)`; return
    the first path or `None`.

- [ ] **Step 5: Run the suite** — Expected: `OK` with no failures (count = Task 2's 64 + the tests added here); the privacy test passes, so every offender is gone; voice script passes.

- [ ] **Step 6: Manual check with the real model** — Close the installed app (it holds port 8791). Run
  `ARABIC_RELAY_DATA_DIR=.local .venv/Scripts/python -m arabic_relay`. In Speech details click "Use a model I already
  have…" and pick the Voice Bridge `data\models\large-v3-…` folder. Record a short Arabic sentence. Expected: a
  transcript appears and `nvidia-smi` lists the python process during transcription. Close it and reopen the
  installed app.

- [ ] **Step 7: Commit (first source commit)** — Run `git status --short` and confirm no file from the never-commit list
  appears. Then:

```bash
git add -A
git commit -m "feat: package layout, per-user data folder, local speech without Voice Bridge"
```

---

### Task 4: `store.py` — databases, settings, vocabulary, Windows secret protection

**Files:**
- Create: `src/arabic_relay/store.py`
- Modify: `src/arabic_relay/app.py`, `tests/test_vocabulary.py`, `tests/test_app.py`, `tests/test_cohere.py`, `tests/test_paths.py`

**Interfaces:**
- Produces: `store.database(vocabulary: bool = False)` (context manager, unchanged body);
  `store.get_setting(key: str, default=None)`, `store.set_setting(key: str, value) -> None`;
  `self_learning_enabled`, `set_self_learning`, `auto_spelling_enabled`, `set_auto_spelling`, `personal_vocabulary`,
  `learn_vocabulary`, `forget_vocabulary`, `ENGLISH_WORD`, `protect(data: bytes, decrypt: bool = False) -> bytes`,
  `AUTH_LOCK`. These are moved unchanged, except the four setting getters and setters, which are rewritten as one-liners
  over `get_setting` / `set_setting`. `prompt_model` / `set_prompt_model` stay in `app.py` until Task 6.
  `app.speech_model_path` and `set_whisper_model` use `store.get_setting('whisper_model_dir')`.

- [ ] **Step 1: Write the failing test** in `tests/test_vocabulary.py`

```python
def test_setting_round_trip(self):
    with patch.object(paths, 'DATA', Path(self.temp.name)):
        self.assertIsNone(store.get_setting('missing'))
        store.set_setting('prompt_model', 'codex')
        self.assertEqual(store.get_setting('prompt_model'), 'codex')
```

- [ ] **Step 2: Run it** — Expected: FAIL (`cannot import name 'store'`).
- [ ] **Step 3: Move the code; replace `app.` patch targets for moved names with `store.`.**
- [ ] **Step 4: Run the suite** — Expected: `OK`, one more test than Task 3; voice script passes.
- [ ] **Step 5: Commit** — `git commit -am "refactor: move storage and settings into store.py"` (`git add` the new file first).

---

### Task 5: `t3.py` — T3 client and the send pipeline

**Files:**
- Create: `src/arabic_relay/t3.py`
- Modify: `src/arabic_relay/app.py`, `src/arabic_relay/desktop.py`, `tests/test_connection.py`, `tests/test_app.py`,
  `tests/test_direct_send.py`, `tests/test_desktop.py`, `tests/test_desktop_chats.py`

**Interfaces:**
- Produces (moved unchanged): `T3`, `OAUTH`, `MCP_SESSION`, `MCP_PROTOCOL`, `load_auth`, `save_auth`, `request_json`,
  `access_token`, `rpc`, `tool`, `catalog`, `normalize_payload`, `prepare_send`, `send_prompt`, `send`, `SEND_LOCK`.
  The OAuth callback and `/api/connect` handler code stays in `app.py` and calls `t3.x`; module globals are set as
  `t3.MCP_SESSION = None` (not `global`). `desktop.py` reads `t3.OAUTH` and `store.AUTH_LOCK`.

- [ ] **Step 1: Move the code; replace patch targets** (`app.tool`, `app.request_json`, `app.urllib.request` within
  T3 tests → `t3.…`).
- [ ] **Step 2: Run the suite** — Expected: `OK`, same count as Task 4. A pure move: no new test; the 11 existing `tool`
  patches and the connection tests are the proof.
- [ ] **Step 3: Commit** — `refactor: move T3 client and send pipeline into t3.py`.

---

### Task 6: `translate.py` — Codex / Claude Code calls

**Files:**
- Create: `src/arabic_relay/translate.py`
- Modify: `src/arabic_relay/app.py`, `tests/test_translation.py`, `tests/test_vocabulary.py`

**Interfaces:**
- Produces (moved unchanged): `SYSTEM_PROMPT`, `SPELLING_PROMPT`, `PROMPT_MODELS`, `prompt_model`, `set_prompt_model`,
  `TRANSLATE_LOCK`, `codex_text`, `restore_english`, `translate`. These use `locate.translator_path()`,
  `locate.claude_path()`, `locate.external_runtime()` and `store.*`. Plan 3 rewrites the model selection; this task
  only moves it.

- [ ] **Step 1: Move the code; replace patch targets** (`app.codex_text`, `app.restore_english`, `app.learn_vocabulary`
  callers → `translate.` / `store.`; `app.subprocess` → `translate.subprocess`).
- [ ] **Step 2: Run the suite** — Expected: `OK`, same count as Task 4.
- [ ] **Step 3: Commit** — `refactor: move translation into translate.py`.

---

### Task 7: `speech.py` — the three speech choices

**Files:**
- Create: `src/arabic_relay/speech.py`
- Modify: `src/arabic_relay/app.py`, `tests/test_speech.py`, `tests/test_cohere.py`

**Interfaces:**
- Produces (moved unchanged): `SPEECH_MODEL`, `COHERE_MODEL`, `MAX_AUDIO`, `TRANSCRIBE_LOCK`, `speech_model_path`,
  `set_whisper_model`, `speech_ready`, `transcribe`, `transcript_result`, `transcribe_cohere`, `NoCohereRedirect`,
  `load_cohere_key`, `save_cohere_key`, `open_wispr_flow`.

- [ ] **Step 1: Move the code; replace patch targets** (`app.speech_model_path`, `app.load_cohere_key`,
  `app.subprocess`, `app.urllib.request.build_opener`, `app.wispr_flow_path` → `speech.` / `locate.`).
- [ ] **Step 2: Run the suite** — Expected: `OK`, same count as Task 4.
- [ ] **Step 3: Commit** — `refactor: move speech choices into speech.py`.

---

### Task 8: `server.py` replaces `app.py`; final verification

**Files:**
- Create: `src/arabic_relay/server.py` (rename of what remains in `app.py`)
- Delete: `src/arabic_relay/app.py`
- Modify: `src/arabic_relay/desktop.py`, `src/arabic_relay/__main__.py`, all tests importing `app`

**Interfaces:**
- Produces: `server.PORT`, `server.ORIGIN`, `server.CSRF`, `server.Handler`, `server.main(open_browser: bool) -> None`,
  `server.ThreadingHTTPServer` (re-export used by `desktop.run`). `desktop.py` uses `server.CSRF`,
  `server.ORIGIN`, `server.PORT`, `server.Handler`; `__main__.py` calls `server.main`.

- [ ] **Step 1: Rename and update imports** — `from arabic_relay import app` → `from arabic_relay import server`
  where a test still drives `Handler`/`CSRF`/`ORIGIN`; `desktop.app.ThreadingHTTPServer` patch →
  `desktop.server.ThreadingHTTPServer`.
- [ ] **Step 2: Confirm nothing references the old module** — `git grep -n "\bapp\b" src tests` lists no import or
  `app.` attribute.
- [ ] **Step 3: Run the suite** — Expected: `OK`, same count as Task 4; voice script passes.
- [ ] **Step 4: Run both modes**
  - Browser: `Start.cmd`, then `curl -s http://127.0.0.1:8788/api/status`. Expected: JSON with `"app": "arabic-relay"`
    and `"modelPath"`.
  - Desktop: close the installed app first, then run `.venv/Scripts/python -m arabic_relay` with
    `ARABIC_RELAY_DATA_DIR` set to `.local`. Expected: the window opens, Settings shows the model folder, and one Arabic
    dictation transcribes and translates. Close it, then reopen the installed app.
- [ ] **Step 5: Commit** — `refactor: server.py replaces app.py`.

---

## Deferred to later plans

- Plan 2:
  - the `--speech-worker` relaunch of the frozen exe;
  - NVIDIA DLLs in `runtime\`;
  - CPU fallback;
  - model download and the setup screen;
  - large-v3-turbo detection;
  - worker crash restart.
- Plan 3:
  - Tool, Model and Effort dropdowns, replacing `PROMPT_MODELS`;
  - login checks;
  - error mapping;
  - path override **Browse…** buttons for Codex, Claude Code and Wispr Flow;
  - Wispr Flow registry lookup.
- Plan 4:
  - PyInstaller folder build, Inno Setup, CI, `--self-test`;
  - updates and signing;
  - single instance and free port;
  - logs and Copy diagnostics;
  - LICENSE, README.md, PRIVACY.md, CONTRIBUTING.md, CHANGELOG.md;
  - gitleaks and the pre-push review of `docs/superpowers/`;
  - the owner switch-over: copy the old `vocabulary\vocabulary.sqlite3` into the data folder, then install.
