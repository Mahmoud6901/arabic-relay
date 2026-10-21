# Plan 3 of 4 — Translation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** The user picks the tool (Codex CLI or Claude Code), the model and the effort for English prompts. The app finds
both tools and Wispr Flow in custom install folders, and every translation failure names the next step.

**Architecture:** `locate.py` gains user overrides (**Browse…**, **Locate Wispr Flow…**), the WinGet links folder and
the Windows installed-programs registry. A new `translators.py` answers "what can the user choose": installed tools,
the live Codex model list from `codex debug models`, effort levels and sign-in state. `translate.py` stores one choice
`{tool, model, effort}`, builds the command from it, and maps failures to the spec's messages. `index.html` replaces
the single Model dropdown with Tool / Model / Effort.

**Tech Stack:** Python 3.13 stdlib (`winreg`, `subprocess`, `json`, `re`), `unittest`, Node for UI tests, pywebview
`FileDialog`.

**Spec:** `docs/superpowers/specs/2026-10-20-public-release-design.md` — Part 3, and the Wispr Flow / Codex / Claude
Code bullets of Part 1 "Locating outside tools and files". Plans 1 and 2 are merged.

## Global Constraints

- Measured on 2026-10-20 (codex-cli 0.163.0, Claude Code 2.1.304):
  - `codex debug models` prints JSON `{"models": [{"slug", "display_name", "visibility", "default_reasoning_level",
    "supported_reasoning_levels": [{"effort", "description"}], …}]}` in about 0.2 s. Only `visibility == "list"`
    models are shown; the effort `ultra` is never shown.
  - `codex login status` prints `Logged in using ChatGPT` and exits 0 when signed in.
  - Unknown Codex model → a JSON event `{"type": "error", "message": "…The 'gpt-nonexistent-9' model is not supported
    when using Codex with a ChatGPT account…"}` and `turn.failed`. Codex also emits `item.completed` with
    `item.type == "error"` for a metadata warning; that is not a failure by itself.
  - Unknown Claude model → `is_error: true`, `api_error_status: 404`, `result: "There's an issue with the selected
    model (claude-nonexistent-9). It may not exist or you may not have access to it."`
  - `claude --effort <level>` accepts `low, medium, high, xhigh, max`.
- Claude Code models: `fable`, `opus`, `sonnet`, `haiku` (shown as Fable, Opus, Sonnet, Haiku). Effort: low, medium, high, xhigh, max.
- Both model lists start with **Default** (model `''`: the tool's own default, no model flag) and end with **Custom…** (free text).
- Effort defaults to `low`. Flags: Codex `-m <model>` and `-c model_reasoning_effort="<effort>"`; Claude `--model <model>` and `--effort <effort>`.
- The same choice serves Prepare English prompt and Improve English spellings.
- Label next to Prepare: `<Tool> · <Model name> · <effort>`, e.g. `Codex · GPT-6.1-Sol · low`, `Claude Code · Default · low`.
- Migration: setting `prompt_model` `codex:gpt-6.1-sol` → `{tool: codex, model: gpt-6.1-sol, effort: low}`; `codex` → model `''`.
- Keep today's isolation flags for both tools and keep removing `OPENAI_API_KEY`, `CODEX_API_KEY`, `ANTHROPIC_API_KEY`. One translation at a time; dictation goes as JSON on stdin.
- Copy (exact):
  - Neither tool: "Install Codex CLI or Claude Code to prepare English prompts. You can still type English yourself."
    Links: `https://developers.openai.com/codex/cli`, `https://code.claude.com/docs/en/overview` (both checked 200 on 2026-10-20).
  - Not signed in: "Codex isn't signed in. Open a terminal and run codex login." / "Claude Code isn't signed in. Open a terminal and run claude, then type /login."
  - Usage limit: "Your Codex usage limit was reached. Switch to Claude Code in Settings or try later." / "Your Claude Code usage limit was reached. Switch to Codex in Settings or try later."
  - Timeout (90 s): "Translation took too long. Try a lower effort."
  - Model removed: "<model> is no longer available, so Arabic Relay switched to the default model."
  - Chosen tool missing, other installed: "<Tool> isn't installed. Choose <Other> in Settings, or type English yourself."
  - Other failure: "<Tool> could not return a draft. Check its sign-in and plan limits, or type English yourself."
- Tests never start the real `codex` or `claude`, never read the real registry (patch `locate.winreg` lookups), and
  patch `translators.catalog` wherever `/api/status` is reached.
- The privacy guard rejects `D:\…`-style paths in tracked files; use `C:\Tools\…` placeholders in tests.
- Test commands: `.venv/Scripts/python -m unittest discover -s tests`, `node tests/test_voice.cjs`, `node tests/test_setup.cjs`, and (from Task 5) `node tests/test_translation_ui.cjs`.

## Review Focus

1. **The owner's saved choice** (`prompt_model = codex:gpt-6.1-sol`) must survive the upgrade as GPT-6.1-Sol · low,
   not silently reset to Default — Task 3 `test_migrates_old_prompt_model`.
2. **`codex debug models` fails, hangs or changes format** must leave Default and Custom… working and never block
   Settings — Task 2 `test_model_list_failure_falls_back` (garbage, nonzero exit, timeout).
3. **A Browse… path that was later deleted** must fall back to auto-detection and be reported, not crash or keep
   using a dead path — Task 1 `test_deleted_override_falls_back_and_is_reported`.
4. **Custom model text** such as `--dangerously-bypass-approvals` or text with spaces or quotes must be refused before
   it reaches a command line — Task 3 `test_custom_model_text_is_validated`.
5. **A removed model** must fall back to Default once and succeed, and must not loop when Default also fails — Task 3
   `test_removed_model_falls_back_once`.

---

### Task 1: `locate.py` — overrides, WinGet links, registry

**Files:**
- Modify: `src/arabic_relay/locate.py`, callers in `firstrun.py`, `server.py`, `speech.py`, `translate.py`
- Test: `tests/test_locate.py`

**Interfaces:**
- Produces:
  - `locate.TOOLS = ('codex', 'claude', 'wispr')`.
  - `locate.tool_path(kind) -> Path | None` — the saved override (setting `<kind>_path`) when it is still a file, else auto-detection.
  - `locate.set_tool_path(kind, path) -> dict` → `{'kind': kind, 'path': str | None}`; `''` clears; refuses a non-file or a non-`.exe`
    with `ValueError("Choose the program file (.exe).")`.
  - `locate.missing_tools() -> dict[str, str]` — kind → saved override path that no longer exists.
  - `locate.registry_install_folders(name) -> list[Path]` — `InstallLocation` of Uninstall entries whose `DisplayName` is `name`, from
    HKCU, HKLM and HKLM\WOW6432Node.
  - Auto-detection adds `%LOCALAPPDATA%\Microsoft\WinGet\Links\codex.exe` / `claude.exe`; Wispr Flow adds the registry folders for `Wispr Flow`.
  - Callers switch from `translator_path()` / `claude_path()` / `wispr_flow_path()` to `tool_path('codex' | 'claude' | 'wispr')`.

- [ ] **Step 1: Write the failing tests** (temp `paths.DATA`, temp `HOME`/`LOCALAPPDATA`/`APPDATA`, `shutil.which` patched to `None`, `locate.registry_install_folders` patched unless under test):

```python
def test_override_wins_over_auto_detection(self):      # set_tool_path('codex', <tmp>/C/Tools/codex.exe) → tool_path('codex') == it
def test_override_must_be_an_exe(self):                # a .txt, a folder, a missing file → ValueError("Choose the program file (.exe).")
def test_deleted_override_falls_back_and_is_reported(self):
    override = make_exe(self.root / 'Tools' / 'codex.exe'); auto = make_exe(self.local / 'Microsoft/WinGet/Links/codex.exe')
    locate.set_tool_path('codex', str(override)); override.unlink()
    self.assertEqual(locate.tool_path('codex'), auto)
    self.assertEqual(locate.missing_tools(), {'codex': str(override)})
def test_clearing_override(self):                      # set_tool_path('codex', '') → {'kind': 'codex', 'path': None}; missing_tools() == {}
def test_winget_links_are_found(self):                 # codex.exe and claude.exe under LOCALAPPDATA/Microsoft/WinGet/Links
def test_wispr_found_through_registry(self):           # registry_install_folders → [<tmp>/Custom/Wispr] with app-1.2/Wispr Flow.exe → found
def test_registry_reader_filters_by_display_name(self):
    # patch locate.winreg with a fake: OpenKey/EnumKey/QueryValueEx over {'A': {'DisplayName': 'Wispr Flow', 'InstallLocation': 'C:\\Apps\\Wispr'},
    # 'B': {'DisplayName': 'Other'}}; one hive raises OSError → registry_install_folders('Wispr Flow') == [Path('C:/Apps/Wispr')]
def test_unknown_kind_is_refused(self):                # tool_path('python') and set_tool_path('python', …) → ValueError
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_locate -v` — new tests fail (`AttributeError: tool_path`).

- [ ] **Step 3: Implement.** `locate` imports `store` (store imports only `paths`; no cycle). The registry walk uses
  `winreg.OpenKey(hive, r'Software\Microsoft\Windows\CurrentVersion\Uninstall')`, enumerates subkeys, and skips any
  `OSError`. Wispr Flow candidates per folder stay as today (`Wispr Flow.exe`, `WisprFlow.exe`, newest `app-*/Wispr Flow.exe`).
  Replace the four callers; keep `translator_path`, `claude_path`, `wispr_flow_path` as the auto-detection helpers `tool_path` falls back to.

- [ ] **Step 4: Run** the full suite — all pass.

- [ ] **Step 5: Commit** — `feat: tool overrides, WinGet links and registry lookup for Wispr Flow`.

---

### Task 2: `translators.py` — what the user can choose

**Files:**
- Create: `src/arabic_relay/translators.py`, `tests/test_translators.py`, `tests/fixtures/codex_models.json`

**Interfaces:**
- Consumes: `locate.tool_path` (Task 1).
- Produces:
  - `translators.CLAUDE_MODELS = (('fable', 'Fable'), ('opus', 'Opus'), ('sonnet', 'Sonnet'), ('haiku', 'Haiku'))`.
  - `translators.EFFORTS = ('low', 'medium', 'high', 'xhigh', 'max')`.
  - `translators.NAMES = {'codex': 'Codex', 'claude': 'Claude Code'}`.
  - `translators.parse_codex_models(text) -> list[dict]` → `[{'id': slug, 'name': display_name, 'efforts': [...]}]`
    (visibility `list` only, `ultra` removed, efforts kept in `EFFORTS` order); anything malformed → `[]`.
  - `translators.codex_models(executable) -> list[dict]` — runs `codex debug models` (timeout 10 s, no window, API keys removed); failure → `[]`.
  - `translators.codex_signed_in(executable) -> bool | None` — `codex login status`: exit 0 and `Logged in` → `True`; other exit → `False`; failure to run → `None`.
  - `translators.catalog(refresh=False) -> dict` →
    `{'codex': {'installed', 'path', 'signedIn', 'models'}, 'claude': {'installed', 'path', 'signedIn', 'models'}, 'efforts': EFFORTS}`,
    cached in the process; `refresh=True` re-runs the Codex commands. Claude `models` = `CLAUDE_MODELS` as dicts with `efforts: EFFORTS`;
    Claude `signedIn` = `False` when the setting `claude_signed_out` is `'1'`, else `None`.
  - `translators.label(choice: dict) -> str` — `'Codex · GPT-6.1-Sol · low'`; model `''` → `Default`; unknown slug → the slug itself.

- [ ] **Step 1: Create the fixture** from real output, trimmed to the fields used:
  `codex debug models | .venv/Scripts/python -c "import json,sys; d=json.load(sys.stdin); print(json.dumps({'models': [{k: m[k] for k in ('slug','display_name','visibility','default_reasoning_level','supported_reasoning_levels')} for m in d['models']]}, indent=1))" > tests/fixtures/codex_models.json`
  Expected: it contains `gpt-6.1-sol` (list, has `ultra`) and `gpt-reserve` (hide).

- [ ] **Step 2: Write the failing tests:**

```python
def test_parses_real_model_list(self):
    models = translators.parse_codex_models(FIXTURE.read_text(encoding='utf-8'))
    sol = next(m for m in models if m['id'] == 'gpt-6.1-sol')
    self.assertEqual(sol, {'id': 'gpt-6.1-sol', 'name': 'GPT-6.1-Sol', 'efforts': ['low', 'medium', 'high', 'xhigh', 'max']})
    self.assertNotIn('gpt-reserve', [m['id'] for m in models])
def test_model_list_failure_falls_back(self):
    for text in ('', 'not json', '{"models": 5}', '{"models": [{"slug": 3}]}'):
        self.assertEqual(translators.parse_codex_models(text), [])
    for outcome in (Mock(returncode=1, stdout='{}'), subprocess.TimeoutExpired('codex', 10), FileNotFoundError()):
        # patch translators.subprocess.run → codex_models(Path('codex.exe')) == []
def test_login_status(self):               # (0, 'Logged in using ChatGPT') → True; (1, 'Not logged in') → False; OSError → None
def test_catalog_marks_missing_tools(self):  # tool_path → None for claude: catalog()['claude']['installed'] False; codex models from patched codex_models
def test_catalog_is_cached_until_refresh(self):  # codex_models called once for two catalog() calls, twice after catalog(refresh=True)
def test_label(self):
    self.assertEqual(translators.label({'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'low'}), 'Codex · GPT-6.1-Sol · low')  # name from the cached catalog
    self.assertEqual(translators.label({'tool': 'claude', 'model': '', 'effort': 'high'}), 'Claude Code · Default · high')
    self.assertEqual(translators.label({'tool': 'claude', 'model': 'opus', 'effort': 'max'}), 'Claude Code · Opus · max')
    self.assertEqual(translators.label({'tool': 'codex', 'model': 'my-model', 'effort': 'low'}), 'Codex · my-model · low')
```

- [ ] **Step 3: Run** `.venv/Scripts/python -m unittest tests.test_translators -v` — fails on import.

- [ ] **Step 4: Implement** `translators.py`. Every subprocess call goes through `locate.external_runtime()` with
  `CREATE_NO_WINDOW` and the three API keys removed from the environment.

- [ ] **Step 5: Run** the suite — all pass. **Commit** — `feat: translators.py lists tools, live Codex models, efforts and sign-in`.

---

### Task 3: `translate.py` — one choice, flags, errors, fallback

**Files:**
- Modify: `src/arabic_relay/translate.py`, `tests/test_translation.py`

**Interfaces:**
- Consumes: `locate.tool_path`, `translators.EFFORTS`, `translators.NAMES`, `translators.label`.
- Produces:
  - `translate.prompt_choice() -> dict` `{'tool', 'model', 'effort'}` — setting `prompt_choice` (JSON); else migrated from `prompt_model`
    (and saved); else `{'tool': <codex if installed, else claude if installed, else codex>, 'model': '', 'effort': 'low'}`.
  - `translate.set_prompt_choice(tool, model, effort) -> dict` → `{'promptChoice': choice, 'promptLabel': str}`.
    Model is `''` or matches `^[A-Za-z0-9][A-Za-z0-9._:\[\]-]{0,79}$`; effort in `EFFORTS`; tool in `('codex', 'claude')`.
  - `translate.build_command(executable, choice, instructions) -> list[str]`.
  - `translate.failure_kind(text) -> str` — `'model'`, `'usage'`, `'login'` or `'other'`, checked in that order.
  - `translate.translate(...)` and `restore_english(...)` results gain `'notice'` after a model fallback; `'translator'` becomes the label.
  - `PROMPT_MODELS`, `prompt_model()`, `set_prompt_model()` are removed.

- [ ] **Step 1: Write the failing tests** (update `test_selected_model_routes_to_codex_or_claude` to the new flags):

```python
def test_migrates_old_prompt_model(self):
    store.set_setting('prompt_model', 'codex:gpt-6.1-sol')
    self.assertEqual(translate.prompt_choice(), {'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'low'})
    self.assertEqual(json.loads(store.get_setting('prompt_choice')), {'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'low'})
    # 'codex' → model ''; 'claude:opus' → {'tool': 'claude', 'model': 'opus', 'effort': 'low'}
def test_custom_model_text_is_validated(self):
    for bad in ('--dangerously-bypass-approvals', 'gpt 6', 'gpt"6', 'a' * 81, '-m'):
        with self.assertRaises(ValueError):
            translate.set_prompt_choice('codex', bad, 'low')
    for bad_effort in ('ultra', 'LOW', ''):
        with self.assertRaises(ValueError):
            translate.set_prompt_choice('codex', '', bad_effort)
def test_codex_flags(self):
    command = translate.build_command('codex.exe', {'tool': 'codex', 'model': 'gpt-6.1-sol', 'effort': 'high'}, 'x')
    self.assertIn('model_reasoning_effort="high"', command)
    self.assertEqual(command[command.index('-m') + 1], 'gpt-6.1-sol')
    self.assertEqual(command[-1], '-')
    self.assertNotIn('-m', translate.build_command('codex.exe', {'tool': 'codex', 'model': '', 'effort': 'low'}, 'x'))
def test_claude_flags(self):               # '--effort', 'max' present; '--model' only when model != ''; '--tools', '' and '--setting-sources', '' kept
def test_failure_kind(self):
    self.assertEqual(translate.failure_kind("The 'gpt-nonexistent-9' model is not supported when using Codex with a ChatGPT account."), 'model')
    self.assertEqual(translate.failure_kind("There's an issue with the selected model (claude-nonexistent-9). It may not exist or you may not have access to it."), 'model')
    self.assertEqual(translate.failure_kind("You've hit your usage limit. Try again later."), 'usage')
    self.assertEqual(translate.failure_kind('401 Unauthorized: please run codex login'), 'login')
    self.assertEqual(translate.failure_kind('something else'), 'other')
def test_removed_model_falls_back_once(self):
    # choice codex/gpt-old/low; first Popen → codex 'error' event with the real "not supported" text; second → agent_message 'Done.'
    # result english 'Done.', notice "gpt-old is no longer available, so Arabic Relay switched to the default model.", saved model ''
    # second case: both runs fail with model text → ValueError(<other-failure copy>), Popen called exactly twice
def test_usage_and_login_messages(self):    # codex error event with each text → the exact Global Constraints copy; claude login failure sets claude_signed_out '1', success clears it
def test_metadata_warning_item_is_not_a_failure(self):  # item.completed type 'error' + agent_message → draft returned
def test_neither_tool_installed(self):     # tool_path → None for both → ValueError(<neither copy>)
def test_chosen_tool_missing_other_installed(self):  # choice claude, only codex installed → "Claude Code isn't installed. Choose Codex in Settings, or type English yourself."
def test_timeout_message(self):            # communicate raises TimeoutExpired(…, 90) → ValueError('Translation took too long. Try a lower effort.'); timeout arg == 90
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_translation -v` — new tests fail.

- [ ] **Step 3: Implement.** Failure text = Codex `error`/`turn.failed` messages (not `item.completed` errors) or Claude
  `result` when `is_error`, plus stderr. Patterns (case-insensitive): model `model.{0,80}(not supported|not found|does not exist|may not exist|unrecognized)|unrecognized_model|issue with the selected model`;
  usage `usage limit|rate limit|quota|too many requests|\b429\b`; login `not logged in|log ?in|sign ?in|unauthori[sz]ed|\b401\b|authenticat`.
  On `'model'` with a non-empty model: save model `''`, retry once inside the same lock, add the notice. Timeout 90 s.

- [ ] **Step 4: Run** the suite — all pass. **Commit** — `feat: tool, model and effort choice with spec error messages and model fallback`.

---

### Task 4: Routes and the desktop file picker

**Files:**
- Modify: `src/arabic_relay/server.py`, `src/arabic_relay/desktop.py`, `tests/test_routes.py`, `tests/test_desktop.py`

**Interfaces:**
- Consumes: Tasks 1–3.
- Produces:
  - `GET /api/translators` → `translators.catalog(refresh=True)` plus `'choice'` and `'label'`.
  - `POST /api/prompt-choice {tool, model, effort}` → `set_prompt_choice`. `POST /api/tool-path {kind, path}` → `locate.set_tool_path`.
  - `/api/prompt-model` is removed. `/api/status`: `promptChoice`, `promptLabel`, `translator` (any tool installed),
    `missingTools`, `wisprFlowInstalled` via `tool_path('wispr')`. Status reads `translators.catalog()` (cached), never `refresh=True`.
  - `DesktopApi.pick_file(token) -> str | None` — `FileDialog.OPEN` with `file_types=('Programs (*.exe)',)`;
    `pick_folder` switches to `webview.FileDialog.FOLDER`.

- [ ] **Step 1: Failing tests:** `test_translator_routes_are_handled` (GET `/api/translators`, POST `/api/prompt-choice`,
  POST `/api/tool-path` are not 404; `/api/prompt-model` is 404); Routes `setUp` patches `translators.catalog`.
  In `test_desktop.py`: `test_file_pickers_check_token_and_use_file_dialog` — wrong token → `ValueError`; with a fake
  `webview.windows[0]`, `pick_file` passes `webview.FileDialog.OPEN` and `file_types=('Programs (*.exe)',)` and returns the first path;
  `pick_folder` passes `webview.FileDialog.FOLDER`.
- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_routes tests.test_desktop -v` — fail.
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run** the suite — all pass. **Commit** — `feat: translator routes and the program file picker`.

---

### Task 5: Settings → English prompt

**Files:**
- Modify: `src/arabic_relay/index.html`, `tests/test_voice.cjs` (status mock: `promptChoice`, `promptLabel`)
- Create: `tests/test_translation_ui.cjs` (copy the `boot()` harness from `tests/test_setup.cjs`)

**Interfaces:**
- Consumes: Task 4 routes; `window.pywebview.api.pick_file(csrf)`.
- Produces ids: `promptTool`, `promptModel`, `promptCustom` (text input, shown only for Custom…), `promptEffort`,
  `promptChoiceLabel` (next to Prepare), `codexStatus`, `claudeStatus`, `codexBrowse`, `claudeBrowse`, `wisprLocate`,
  `toolHelp` (neither-installed copy with the two links). Option values: models `''` (Default), ids, `custom`.

- [ ] **Step 1: Write `tests/test_translation_ui.cjs`** asserting:
  - Opening Settings GETs `/api/translators`; `promptTool` options for a missing tool are `disabled`.
  - Codex models fill `promptModel` as `Default`, `GPT-6.1-Sol`, …, `Custom…`; Claude shows `Default`, `Fable`, `Opus`, `Sonnet`, `Haiku`, `Custom…`.
  - `promptEffort` lists the selected model's efforts (no `ultra`); Default lists `low … max`.
  - Changing Tool, Model or Effort POSTs `/api/prompt-choice` with `{tool, model, effort}` and sets `promptChoiceLabel` to the returned label.
  - Custom…: shows `promptCustom`; Enter POSTs its text as `model`; an error reply restores the previous selection and shows the message.
  - `codexStatus` shows `Codex · signed in` / `Codex isn't signed in. Open a terminal and run codex login.` / `Codex · not found`.
  - `codexBrowse` (native only) calls `pick_file` and POSTs `/api/tool-path {kind: 'codex', path}`; `wisprLocate` does the same with `kind: 'wispr'`.
  - Neither installed → `toolHelp` visible (its copy and both links are checked in the page source).
  - A `/api/translate` reply with `notice` shows it; `translator` replaces the old "Prompt model set to …" text.
  - `missingTools: {codex: 'C:\\Tools\\codex.exe'}` in status → notice `Codex was moved or deleted: C:\Tools\codex.exe. Arabic Relay uses the copy it finds, or choose Browse… again.`
- [ ] **Step 2: Run** `node tests/test_translation_ui.cjs` — fails.
- [ ] **Step 3: Implement.** Remove the old `#promptModel` options and the `/api/prompt-model` handler.
- [ ] **Step 4: Run** all four test commands — pass. **Commit** — `feat: tool, model and effort dropdowns in Settings`.

---

### Task 6: Real-machine check

- [ ] **Step 1: Real translations** with `ARABIC_RELAY_DATA_DIR` set to a new temp folder (no window):
  1. `set_prompt_choice('codex', 'gpt-6.1-sol', 'low')`, translate `ضيف زرار dark mode في صفحة الإعدادات` → an English sentence mentioning dark mode.
  2. `set_prompt_choice('claude', 'sonnet', 'low')`, same input → English text.
  3. `set_prompt_choice('codex', 'gpt-nonexistent-9', 'low')` → result has the fallback notice and English text; the saved model is `''`.
  4. `translators.catalog(refresh=True)` lists the live Codex models and `signedIn: True`.
- [ ] **Step 2: Isolated window** (the Plan 2 launcher: temp data folder, port 8796): Settings shows Tool / Model / Effort
  filled from the live list; picking a model and effort updates the label next to Prepare; Browse… for Codex opens the
  file dialog (cancel it); the window never touches the installed app. Delete the temp folder afterwards.
- [ ] **Step 3: Commit** any copy fixes found — `fix: translation settings copy from the real-machine check`.

---

## Deferred

- Plan 4: installer, CI, updates, logs, Copy diagnostics, docs, owner switch-over.
- Not planned: API-key mode, local translation models (spec "Out of scope for v1").
