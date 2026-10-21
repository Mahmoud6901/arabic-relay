# Plan 2 of 4 — Speech Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A new user with no model, no CUDA and no Python gets working local dictation from inside the window: the
app checks the computer, downloads the right Whisper model (and the NVIDIA library only when a GPU is present), tests
it, and falls back to the CPU on its own when the GPU can't run it.

**Architecture:** The app relaunches itself for heavy work: `--speech-worker` (one transcription) and `--setup`
(downloads). `paths.self_command(*args)` builds that command for both the development venv and the later frozen exe,
so Plan 4 changes nothing here. `models.py` owns everything on disk (model folders, the cuBLAS runtime, GPU detection,
downloads); `firstrun.py` runs the setup job and the post-download check for the server; `index.html` gets a setup
dialog and richer Transcription settings.

**Tech Stack:** Python 3.13 stdlib (`subprocess`, `zipfile`, `hashlib`, `urllib`), `huggingface_hub` (already a
faster-whisper dependency), faster-whisper/CTranslate2, `unittest`, Node for UI tests.

**Spec:** `docs/superpowers/specs/2026-10-20-public-release-design.md` — Part 2, the speech rows of Part 6, and the
`models.py` / `speech_worker.py` rows of Part 1. Plan 1 (`docs/superpowers/plans/2026-10-20-plan-1-foundation.md`)
is merged.

## Global Constraints

- Pinned downloads (verified 2026-10-20):

  | Item | Source | Pin | Bytes |
  |---|---|---|---|
  | `large-v3` | `Systran/faster-whisper-large-v3` | revision `edaa852ec7e145841d8ffdb056a99866b5f0a478` | 3,090,835,702 |
  | `large-v3-turbo` | `mobiuslabsgmbh/faster-whisper-large-v3-turbo` (faster-whisper's own `turbo` mapping) | revision `0a363e9161cbc7ed1431c9597a8ceaf0c4f78fcf` | 1,621,665,983 |
  | cuBLAS wheel | `https://files.pythonhosted.org/packages/20/e2/fc9a0e985249d873150276d5afb02e39a66817fedbf1a385724393e505ed/nvidia_cublas_cu12-12.9.2.10-py3-none-win_amd64.whl` | sha256 `623f43027d40d44ceadf0043f002bd25cf353e8f13ce90b9a87057019f560661` | 553,162,896 |
  | extracted DLLs | `nvidia/cublas/bin/cublas64_12.dll`, `nvidia/cublas/bin/cublasLt64_12.dll` | — | 771,191,808 |

- **GPU runtime is cuBLAS only.** Measured on 2026-10-20: CTranslate2 4.8.3 ships its own `cudnn64_9.dll`, and a
  large-v3 GPU transcription loads only `cublas64_12.dll` and `cublasLt64_12.dll` from outside. No cuDNN or NVRTC
  download. (Task 7 updates the spec's "~1 GB" and cuDNN wording.)
- Model download allow-list: `config.json`, `model.bin`, `tokenizer.json`, `vocabulary.*`, `preprocessor_config.json`.
- Downloads go to `paths.DATA / 'models' / <name>` (the model) and `paths.DATA / 'runtime'` (the DLLs).
- The download child runs with `HF_HUB_DISABLE_XET=1` (plain HTTPS, resumable `.incomplete` files),
  `HF_HUB_DISABLE_TELEMETRY=1`, and `token=False` (never send a user's Hugging Face token).
- The speech worker keeps `HF_HUB_OFFLINE=1` and `local_files_only=True`: dictation makes no network calls.
- Child processes never receive `OPENAI_API_KEY`, `CODEX_API_KEY` or `ANTHROPIC_API_KEY`.
- **Never delete a folder outside `paths.DATA / 'models'`.** A model the user picked elsewhere can be forgotten, not deleted.
- Devices: GPU = `device='cuda', compute_type='float16'`; CPU = `device='cpu', compute_type='int8'`.
- Copy (exact):
  - No GPU: "No NVIDIA GPU, so transcription will run on the CPU and take a few seconds per sentence."
  - GPU fallback for one recording: "The GPU was unavailable, so this recording ran on the CPU."
  - GPU failed the setup check: "The GPU couldn't run the speech model, so speech will use the CPU. You can turn this off in Settings."
  - Worker crashed twice: "The speech engine stopped. Record again to restart it."
  - Not enough disk: "Free up space first: this download needs {need} GB and {free} GB is free." (one decimal)
  - Download network failure: "The download stopped. Check your internet connection and try again; it resumes where it left off."
- Do not rebuild or replace the installed `Arabic Relay.exe`. Window checks use the isolated launcher (Task 7), never port 8791.
- Development runs use `ARABIC_RELAY_DATA_DIR=.local`. Tests patch `paths.DATA` to a temporary folder and never call
  the network, `nvidia-smi`, or the real T3 service.
- Test commands: `.venv/Scripts/python -m unittest discover -s tests` and `node tests/test_voice.cjs` and (from Task 5)
  `node tests/test_setup.cjs`.

## Review Focus

1. **Delete model on a folder the user picked** (`C:\my models\large-v3`) must refuse and leave it intact — Task 1
   `test_delete_refuses_folder_outside_app_models`.
2. **Cancel, then Download again** must resume, not wipe the partial files — Task 3 `test_fetch_resumes_partial_file`
   and Task 4 `test_cancel_keeps_partial_files`.
3. **Model or data folder with Arabic letters and spaces** must reach the worker and the setup child unchanged —
   Task 2 `test_worker_command_keeps_arabic_path`.
4. **Window closed mid-download** must stop the download child, not leave it running — Task 3
   `test_setup_child_watches_parent`.
5. **GPU present but CUDA can't run** (old driver, out of memory) must end with working CPU speech and a message, not
   an error loop — Task 2 `test_cuda_failure_falls_back_to_cpu` and Task 4 `test_gpu_check_failure_switches_to_cpu`.

---

### Task 1: `models.py` — what's on disk and what the computer has

**Files:**
- Create: `src/arabic_relay/models.py`, `tests/test_models.py`
- Modify: `src/arabic_relay/locate.py` (`HF_REPOS`, `whisper_model`)

**Interfaces:**
- Produces:
  - `models.MODELS: dict[str, dict]` — keys `'large-v3'`, `'large-v3-turbo'`; values `{'repo', 'revision', 'bytes'}` from Global Constraints.
  - `models.GPU_WHEEL = {'url', 'sha256', 'bytes', 'files', 'extracted_bytes'}`.
  - `models.models_dir() -> Path`, `models.runtime_dir() -> Path` (computed from `paths.DATA` at call time).
  - `models.gpu_info() -> dict | None` → `{'name': str, 'vramGb': float, 'driver': str}`; cached for the process (`functools.cache`).
  - `models.gpu_runtime_dir() -> Path | None` — first folder holding both `cublas64_12.dll` and `cublasLt64_12.dll`.
  - `models.model_label(folder) -> str` — `'Whisper large-v3-turbo'`, `'Whisper large-v3'` or `'Whisper (your model)'`.
  - `models.app_owned(folder) -> bool`, `models.delete_model(folder) -> None`.
  - `models.free_bytes() -> int`, `models.bytes_needed(name, gpu_download: bool) -> int`.
  - `locate.whisper_model(saved)` now also finds `models_dir()/large-v3`, `models_dir()/large-v3-turbo` and the turbo HF cache repo.

- [ ] **Step 1: Write the failing tests** in `tests/test_models.py` (temp `paths.DATA`; `make_model` as in `test_speech.py`):

```python
def test_gpu_info_parses_nvidia_smi(self):
    done = Mock(returncode=0, stdout='NVIDIA GeForce RTX 4080 Laptop GPU, 12282, 616.92\n')
    with patch.object(models.subprocess, 'run', return_value=done) as run:
        self.assertEqual(models.gpu_info(), {'name': 'NVIDIA GeForce RTX 4080 Laptop GPU', 'vramGb': 12.0, 'driver': '616.92'})
    self.assertEqual(run.call_args.args[0][:2], ['nvidia-smi', '--query-gpu=name,memory.total,driver_version'])

def test_gpu_info_is_none_without_driver(self):      # FileNotFoundError, returncode 9, and garbage stdout → None each
def test_gpu_runtime_prefers_app_runtime(self):      # both DLLs in DATA/runtime → that folder
def test_gpu_runtime_needs_both_dlls(self):          # only cublas64_12.dll anywhere → None
def test_gpu_runtime_uses_cuda_path(self):           # CUDA_PATH=<tmp>, DLLs in <tmp>/bin → <tmp>/bin
def test_model_label(self):
    for folder, label in (('x/models/large-v3-turbo', 'Whisper large-v3-turbo'),
                          ('x/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/0a3', 'Whisper large-v3-turbo'),
                          ('E/models/large-v3-edaa852', 'Whisper large-v3'), ('D:/نماذج/my model', 'Whisper (your model)')):
        self.assertEqual(models.model_label(Path(folder)), label)
def test_downloaded_model_is_found(self):            # make_model(DATA/models/large-v3-turbo); locate.whisper_model(None) == it
def test_turbo_in_hf_cache_is_found(self):           # HF_HUB_CACHE=<tmp>; make_model(<tmp>/models--mobiuslabsgmbh--faster-whisper-large-v3-turbo/snapshots/a)
def test_delete_refuses_folder_outside_app_models(self):
    outside = make_model(Path(self.temp) / 'my models' / 'large-v3')
    with self.assertRaises(ValueError):
        models.delete_model(outside)
    self.assertTrue((outside / 'model.bin').exists())
def test_delete_removes_app_model(self):             # make_model(DATA/models/large-v3); delete; folder gone
def test_bytes_needed(self):
    self.assertEqual(models.bytes_needed('large-v3-turbo', False), 1_621_665_983 + 200_000_000)
    self.assertEqual(models.bytes_needed('large-v3', True), 3_090_835_702 + 553_162_896 + 771_191_808 + 200_000_000)
```

The `gpu_runtime_*` tests patch `models.site.getsitepackages` to `[]` and clear `CUDA_PATH` so the real venv DLLs
don't leak in. Each test calls `models.gpu_info.cache_clear()` in `setUp`.

- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_models -v` — expect `ModuleNotFoundError: arabic_relay.models`.

- [ ] **Step 3: Implement `models.py` and the `locate.whisper_model` additions.**
  - `gpu_info`: `subprocess.run(['nvidia-smi', '--query-gpu=name,memory.total,driver_version', '--format=csv,noheader,nounits'], capture_output=True, text=True, timeout=5, creationflags=CREATE_NO_WINDOW)` inside `locate.external_runtime()`; first line only; `vramGb = round(MiB / 1024, 1)`; any exception or bad line → `None`.
  - `gpu_runtime_dir` candidates, in order: `runtime_dir()`, each `site.getsitepackages()` entry `/nvidia/cublas/bin`, `$CUDA_PATH/bin`.
  - `app_owned(folder)`: `Path(folder).resolve().is_relative_to(models_dir().resolve())`. `delete_model` raises
    `ValueError('Arabic Relay only deletes models it downloaded. Remove this folder yourself if you no longer need it.')` otherwise, then `shutil.rmtree`.
  - `bytes_needed`: model bytes, plus wheel + extracted bytes when `gpu_download`, plus a 200 MB margin.
  - `locate.HF_REPOS` gains `'models--mobiuslabsgmbh--faster-whisper-large-v3-turbo'`; `whisper_model(saved)` order:
    saved, `models_dir()/large-v3`, `models_dir()/large-v3-turbo`, then the HF cache repos. `locate` must not import
    `models` (models imports locate); compute the app folder there as `paths.DATA / 'models' / name`.

- [ ] **Step 4: Run** the full suite — all pass (82 existing + new).

- [ ] **Step 5: Commit** — `feat: models.py finds GPU, cuBLAS runtime and downloaded models`.

---

### Task 2: The worker runs from the app itself, picks a device, survives crashes

**Files:**
- Modify: `src/arabic_relay/paths.py`, `src/arabic_relay/__main__.py`, `src/arabic_relay/speech_worker.py`,
  `src/arabic_relay/speech.py`, `src/arabic_relay/server.py` (`/api/status`), `tests/test_speech.py`
- Create: `tests/test_worker_real.py`

**Interfaces:**
- Consumes: `models.gpu_info`, `models.gpu_runtime_dir`, `models.model_label` (Task 1).
- Produces:
  - `paths.self_command(*args: str) -> list[str]` — frozen: `[sys.executable, *args]`; development: `[sys.executable, '-I', '-m', 'arabic_relay', *args]`.
  - `python -m arabic_relay --speech-worker <parent_pid> <model_folder> <cuda|cpu>` (no `webview` import on this path).
  - `speech_worker.run_model(samples, folder: str, device: str, keywords: list[str]) -> dict` → `{'text', 'device': 'GPU'|'CPU', 'fallback': bool}`.
  - `speech.speech_device() -> str` — `'cuda'` when the `speech_device` setting isn't `'cpu'`, `models.gpu_info()` is not None and `models.gpu_runtime_dir()` is not None; else `'cpu'`.
  - `speech.set_cpu_only(enabled: bool) -> dict` → `{'cpuOnly': bool}` (setting `speech_device` = `'cpu'` / `'auto'`).
  - `speech.run_worker(audio: bytes, keywords: list[str], check=False) -> dict` — one launch, one automatic relaunch on a crash.
  - `speech.check() -> dict` → `{'device': 'GPU'|'CPU', 'fallback': bool}` — 2 s of silent 16 kHz WAV through the worker with `check=True`.
  - `speech.transcribe(...)` result gains `'device'` and, after a fallback, `'notice'`; `'model'` becomes `models.model_label(folder)`.
  - `/api/status` gains `speechModel` (label or `None`), `speechDevice` (`'GPU'`/`'CPU'`), `cpuOnly`, `gpu` (name or `None`), `modelOwned`.

- [ ] **Step 1: Write the failing tests** in `tests/test_speech.py`:

```python
def test_worker_command_dev(self):          # paths.self_command('--speech-worker', '1', 'f', 'cpu')[:5] == [sys.executable, '-I', '-m', 'arabic_relay', '--speech-worker']
def test_worker_command_frozen(self):       # patch.object(sys, 'frozen', True, create=True) → [sys.executable, '--speech-worker', ...]
def test_worker_command_keeps_arabic_path(self):
    folder = Path('D:/نماذج/large v3')
    with patch.object(speech, 'speech_model_path', return_value=folder), patch.object(speech, 'speech_device', return_value='cpu'), \
            patch.object(speech.subprocess, 'Popen', return_value=self.process({'text': 'نص', 'device': 'CPU', 'fallback': False})) as launch:
        speech.transcribe(b'\x1aE\xdf\xa3fake', 'audio/webm')
    self.assertEqual(launch.call_args.args[0][-3:], [str(os.getpid()), str(folder), 'cpu'])
def test_device_is_cpu_without_gpu(self):   # gpu_info None → 'cpu'
def test_cpu_only_setting_forces_cpu(self): # gpu present + runtime present + set_cpu_only(True) → 'cpu'; set_cpu_only(False) → 'cuda'
def test_crash_relaunches_once(self):       # first process returncode 3221225477, stdout b''; second ok → text returned; Popen called twice
def test_second_crash_reports_engine_stopped(self):
    # both crash → ValueError('The speech engine stopped. Record again to restart it.'); Popen called exactly twice
def test_worker_error_code_is_not_retried(self):  # {'error': 'INVALID_AUDIO'}, returncode 1 → one launch, decode message
def test_gpu_fallback_notice(self):         # worker {'text': 'x', 'device': 'CPU', 'fallback': True} → result['notice'] == 'The GPU was unavailable, so this recording ran on the CPU.'
def test_cuda_failure_falls_back_to_cpu(self):
    calls = []
    def fake_model(folder, device, compute_type, local_files_only):
        calls.append((device, compute_type))
        if device == 'cuda':
            raise RuntimeError('CUDA failed with error out of memory')
        return Mock(transcribe=Mock(return_value=(iter([Mock(text=' مرحبا')]), None)))
    with patch('faster_whisper.WhisperModel', side_effect=fake_model):
        result = speech_worker.run_model([0.0] * 16000, 'folder', 'cuda', [])
    self.assertEqual(calls, [('cuda', 'float16'), ('cpu', 'int8')])
    self.assertEqual(result, {'text': 'مرحبا', 'device': 'CPU', 'fallback': True})
```

Update `test_mixed_words_reach_only_local_worker`: the command now contains `'--speech-worker'` (replace the
`speech_worker.py` assertion); `HF_HUB_OFFLINE` stays `'1'`. Replace Plan 1's `test_worker_runs_with_this_interpreter`
with the two `test_worker_command_*` tests.

`tests/test_worker_real.py` (skips unless `locate.validate_model_folder(locate.hf_cache() / 'models--Systran--faster-whisper-tiny')` succeeds):

```python
def test_real_worker_transcribes_english_on_cpu(self):
    # Synthesize "hello world" with System.Speech (PowerShell, 16 kHz mono WAV) into a temp file, run
    # paths.self_command('--speech-worker', str(os.getpid()), str(tiny), 'cpu') with the same stdin protocol and env as
    # speech.run_worker, and assert 'hello' in result['text'].lower() and result['device'] == 'CPU'.
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_speech -v` — the new tests fail (`AttributeError: self_command` / `run_model` / `speech_device`).

- [ ] **Step 3: Implement.**
  - `__main__.py`: dispatch `--speech-worker` → `speech_worker.main(argv_after_flag)` and `--setup` → `models.setup_main(argv_after_flag)` (Task 3 adds it; add the branch now) before any `desktop` import.
  - `speech_worker.main(args)`: `args = [parent_pid, folder, device]`; DLL directory = `models.gpu_runtime_dir()` (only when `device == 'cuda'`), replacing the `site-packages/nvidia/*/bin` scan. Header JSON may carry `"check": true`: then skip `vad_filter` and the empty-text check. Output adds `device` and `fallback`. Keep the parent watch, the 301 s bound and the error codes.
  - `speech_worker.run_model`: import `WhisperModel` inside; try the requested device; on any exception from model load or the first transcription with `device == 'cuda'`, print the error to stderr and retry once on CPU int8.
  - `speech.run_worker`: build `paths.self_command('--speech-worker', str(os.getpid()), str(folder), speech_device())`, keep today's env, timeout and stdin protocol. A crash is `returncode != 0` and no `error` key in stdout JSON; log the stderr tail with `logging.getLogger(__name__).error` and relaunch once.
  - `speech.transcribe` keeps all validation and `TRANSCRIBE_LOCK`; it calls `run_worker`. `check()` takes the lock too.
  - The `LOCAL_SPEECH_FAILED` message becomes "Local speech failed on this recording. Record again; if it keeps failing, turn on Always use CPU in Settings."
  - `/api/status`: add the fields listed in Interfaces.

- [ ] **Step 4: Run** the suite. Then download the tiny model once and run the real test:
  `.venv/Scripts/python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-tiny')"`
  then `.venv/Scripts/python -m unittest tests.test_worker_real -v` — expect `OK`, not `skipped`.

- [ ] **Step 5: Commit** — `feat: speech worker runs from the app, picks GPU or CPU, relaunches after a crash`.

---

### Task 3: Downloads — model and cuBLAS, in a `--setup` child

**Files:**
- Modify: `src/arabic_relay/models.py`, `tests/test_models.py`

**Interfaces:**
- Consumes: `models.MODELS`, `models.GPU_WHEEL`, `models.models_dir/runtime_dir` (Task 1); `speech_worker.watch_parent` (existing).
- Produces:
  - `models.fetch(url, dest: Path, sha256: str, size: int, report) -> Path` — resumable HTTPS download via `dest.with_suffix('.part')` and a `Range: bytes=<n>-` header; verifies sha256; renames to `dest`.
  - `models.install_gpu_runtime(wheel: Path) -> Path` — extracts exactly `GPU_WHEEL['files']` into `runtime_dir()` (flat), deletes the wheel, returns `runtime_dir()`.
  - `models.download_model(name: str, report) -> Path` — `huggingface_hub.snapshot_download(repo_id, revision=..., local_dir=models_dir()/name, allow_patterns=[...], token=False)` with a thread reporting the folder's on-disk bytes every 0.5 s; returns `locate.validate_model_folder(...)`.
  - `report(stage: str, done: int, total: int)` — stage `'model'` or `'gpu'`.
  - `models.setup_main(args)` — `args = [parent_pid, name, '1'|'0']` (download GPU runtime?). Prints one JSON object per line: `{"stage", "done", "total"}` while running; finally `{"modelPath": str}` or `{"error": str}` (exit code 1).

- [ ] **Step 1: Write the failing tests.** Serve bytes from a local `ThreadingHTTPServer` in the test whose handler honors `Range: bytes=N-` (206 + tail) and records the header it saw.

```python
def test_fetch_resumes_partial_file(self):
    data = os.urandom(300_000); dest = self.root / 'w.whl'
    dest.with_suffix('.part').write_bytes(data[:100_000])
    models.fetch(self.url, dest, hashlib.sha256(data).hexdigest(), len(data), lambda *a: None)
    self.assertEqual(self.ranges, ['bytes=100000-'])
    self.assertEqual(dest.read_bytes(), data)
def test_fetch_rejects_wrong_hash(self):        # ValueError; neither dest nor .part remains
def test_install_gpu_runtime_keeps_only_cublas(self):
    # zip with nvidia/cublas/bin/{cublas64_12,cublasLt64_12,nvblas64_12}.dll + a .py → runtime_dir has exactly the two; wheel deleted
def test_download_model_pins_revision_and_files(self):
    # patch('huggingface_hub.snapshot_download', side_effect=lambda **kw: make_model(Path(kw['local_dir'])))
    # assert kwargs: repo_id, revision (Global Constraints), local_dir == DATA/models/large-v3, token is False,
    # allow_patterns == ['config.json', 'model.bin', 'tokenizer.json', 'vocabulary.*', 'preprocessor_config.json']
def test_setup_main_prints_progress_then_path(self):  # patched downloads; last stdout line {'modelPath': ...}; gpu stage only when '1'
def test_setup_main_maps_network_error(self):         # download_model raises OSError → last line {'error': <network copy>}, exit 1
def test_setup_child_watches_parent(self):            # patch speech_worker.watch_parent; setup_main(['4242', ...]) calls it with 4242
```

- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_models -v` — new tests fail.

- [ ] **Step 3: Implement.** `fetch` streams 1 MB chunks with `urllib.request.urlopen(Request(url, headers=range), timeout=60)`; a 200 reply to a range request restarts the file. `setup_main` sets `HF_HUB_DISABLE_XET`, `HF_HUB_DISABLE_TELEMETRY` before importing `huggingface_hub`; it maps `OSError`/`urllib.error.URLError` to the network copy and other `ValueError`s to their message; it downloads the wheel to `runtime_dir()/cublas.whl` only when the argument is `'1'` and `gpu_runtime_dir()` is `None`.

- [ ] **Step 4: Run** the suite — all pass.

- [ ] **Step 5: Commit** — `feat: resumable model and cuBLAS downloads in a --setup child`.

---

### Task 4: `firstrun.py` — setup job, check and API

**Files:**
- Create: `src/arabic_relay/firstrun.py`, `tests/test_firstrun.py`
- Modify: `src/arabic_relay/server.py`, `src/arabic_relay/speech.py` (`delete_current_model`)

**Interfaces:**
- Consumes: Task 1 helpers, `paths.self_command`, `speech.check`, `models.setup_main` protocol (Task 3).
- Produces:
  - `firstrun.checklist() -> dict` → `{'gpu': gpu_info(), 'gpuRuntimeReady': bool, 'codex': bool, 'claude': bool, 'wisprFlow': bool, 'freeGb': float, 'recommended': 'large-v3' if gpu else 'large-v3-turbo', 'models': {name: {'gb': float}}, 'gpuDownloadGb': 0.55 or 0}`.
  - `firstrun.setup_needed() -> bool` — no model found and setting `setup_done` unset.
  - `firstrun.skip() -> dict` — sets `setup_done`; returns `{'setupNeeded': False}`.
  - `firstrun.JOB: SetupJob` with `start(name, fresh=False) -> dict`, `progress() -> dict`, `cancel() -> dict`, attribute `thread`.
    Progress: `{'state': 'idle'|'downloading'|'checking'|'done'|'error'|'cancelled', 'stage', 'done', 'total', 'error', 'device', 'notice'}`.
  - `speech.delete_current_model() -> dict` — `models.delete_model(current)` then clears `whisper_model_dir`; returns `{'speechConfigured': bool}`.
  - Routes: `GET /api/setup`, `GET /api/setup/progress`, `POST /api/setup/download {model, fresh}`, `POST /api/setup/cancel`,
    `POST /api/setup/skip`, `POST /api/speech-device {cpuOnly}`, `POST /api/model/delete`. `/api/status` gains
    `setupNeeded` and `missingModel` (the saved `whisper_model_dir` when it no longer validates, else `None`).

- [ ] **Step 1: Write the failing tests** (fake child: a `Mock` whose `stdout` iterates prepared JSON lines and whose `wait()` returns the code; patch `speech.check`):

```python
def test_start_refuses_when_disk_is_short(self):
    with patch.object(models, 'free_bytes', return_value=1_000_000_000), patch.object(models, 'gpu_info', return_value=None):
        with self.assertRaisesRegex(ValueError, r'^Free up space first: this download needs 1\.8 GB and 1\.0 GB is free\.$'):
            firstrun.JOB.start('large-v3-turbo')
def test_job_runs_child_then_check(self):     # lines progress→modelPath; check → GPU; state 'done', whisper_model_dir and setup_done saved
def test_gpu_check_failure_switches_to_cpu(self):  # check → {'device': 'CPU', 'fallback': True}; setting speech_device == 'cpu'; notice == GPU-failed copy
def test_child_error_is_reported(self):        # last line {'error': 'x'} → state 'error', error 'x'; whisper_model_dir unset
def test_cancel_keeps_partial_files(self):     # partial file in DATA/models/large-v3; cancel → process.kill called, state 'cancelled', file still there
def test_fresh_start_removes_app_model_first(self)  # existing DATA/models/large-v3 deleted before the child starts
def test_second_start_is_refused(self):        # while state 'downloading' → ValueError('A download is already running.')
def test_unknown_model_is_refused(self):       # start('tiny') → ValueError
def test_setup_needed(self):                   # True with nothing; False after skip(); False with a found model
def test_delete_current_model_only_when_app_owned(self)  # outside folder → ValueError, setting kept
def test_status_reports_missing_saved_folder(self):     # saved whisper_model_dir that no longer exists → /api/status missingModel == it, speechConfigured False
```

`tests/test_routes.py` already proves every `/api/...` URL in `index.html` is routed; it covers the new routes once
Task 5 adds them to the page. Every test that reaches `/api/status` or `/api/setup` (Routes, `test_app.py`) patches
`models.gpu_info` to return `None`, so no test runs `nvidia-smi`.

- [ ] **Step 2: Run** `.venv/Scripts/python -m unittest tests.test_firstrun -v` — fails on import.

- [ ] **Step 3: Implement.** `start` checks the name, the running state, `bytes_needed(name, gpu_download)` against
  `free_bytes()` (`gpu_download = gpu_info() is not None and gpu_runtime_dir() is None`), deletes the app-owned folder when
  `fresh`, launches `paths.self_command('--setup', str(os.getpid()), name, '1'|'0')` (`stdout=PIPE`, no window, env
  without the API keys), and starts a daemon thread that parses lines into the progress dict. On `modelPath`: state
  `'checking'`, save `whisper_model_dir`, run `speech.check()`; on fallback with a GPU present set `speech_device=cpu`
  and the notice; set `setup_done`; state `'done'`. Any check error → state `'error'` with its message. One `threading.Lock` guards the dict.

- [ ] **Step 4: Run** the suite — all pass.

- [ ] **Step 5: Commit** — `feat: first-run setup job with disk check, cancel, and GPU self-check`.

---

### Task 5: Setup screen

**Files:**
- Modify: `src/arabic_relay/index.html`
- Create: `tests/test_setup.cjs`

**Interfaces:**
- Consumes: the Task 4 routes and `/api/status.setupNeeded`; `window.pywebview.api.pick_folder(csrf)` (existing).
- Produces: `<dialog id="setupDialog">` with ids `setupGpu`, `setupCodex`, `setupClaude`, `setupWispr`, `setupDisk`,
  `setupCpuWarning`, `setupModel` (select: `large-v3`, `large-v3-turbo`, option text `Whisper large-v3 · 3.1 GB` /
  `Whisper large-v3-turbo · 1.6 GB · faster`), `setupDownloadSize`, `setupStart`, `setupPick`, `setupSkip`,
  `setupProgress` (`<progress>`), `setupProgressText`, `setupCancel`, `setupError`; JS `openSetup(fresh=false)`.

- [ ] **Step 1: Write `tests/test_setup.cjs`.** Copy the fake DOM / `vm` harness from `test_voice.cjs` into a
  `boot(status, setup)` function so each scenario gets a fresh context; `fetch` answers the setup routes from a script.
  Assertions:
  - GPU (`gpu: {name: 'NVIDIA GeForce RTX 4080 Laptop GPU', vramGb: 12}`): dialog opens on load; `setupGpu` text is
    `NVIDIA GeForce RTX 4080 Laptop GPU · 12 GB`; `setupModel.value === 'large-v3'`; `setupCpuWarning.hidden`.
  - No GPU: `setupModel.value === 'large-v3-turbo'`; `setupCpuWarning` shows the exact no-GPU copy.
  - Start: POSTs `/api/setup/download` with `{model: 'large-v3', fresh: false}`; a progress reply
    `{state: 'downloading', stage: 'model', done: 1.5e9, total: 3.09e9}` renders `setupProgressText` starting with
    `Downloading the speech model · 1.5 of 3.1 GB`; a `done` reply closes the dialog and shows notice `Speech is ready.`
    (plus the job's `notice` when present).
  - Error reply: `setupError` shows the message; `setupStart` is enabled again and reads `Try again`.
  - Skip: POSTs `/api/setup/skip`, sets `dictationProvider` to `wispr` (and `localStorage['relay-dictation-provider']`), closes.
  - `setupPick` is hidden unless `nativeDesktop`.
  - `setupNeeded: false` → the dialog never opens.

- [ ] **Step 2: Run** `node tests/test_setup.cjs` — fails (`setupDialog` missing).

- [ ] **Step 3: Implement** the dialog in the existing settings-dialog style. Checklist lines: GPU (name · VRAM, or "No
  NVIDIA GPU"), Codex / Claude Code / Wispr Flow ("found" / "not found"), "Free space: N GB", and "Downloads: <model
  size> + NVIDIA library 0.55 GB" (library only when `gpuDownloadGb`). Poll `/api/setup/progress` every second while
  `downloading`/`checking`; speed in MB/s from the last two polls; stage text `Downloading the speech model`,
  `Downloading the NVIDIA GPU library`, `Testing speech…`. Cancel POSTs `/api/setup/cancel` and shows "Paused. Download again to resume.".
  `setupPick` reuses the `pickModel` flow, then closes the dialog when the model saves.

- [ ] **Step 4: Run** `node tests/test_setup.cjs`, `node tests/test_voice.cjs` and the Python suite (Routes now sees the new URLs) — all pass.

- [ ] **Step 5: Commit** — `feat: first-launch setup screen`.

---

### Task 6: Settings → Transcription

**Files:**
- Modify: `src/arabic_relay/index.html`, `tests/test_setup.cjs`, `tests/test_voice.cjs`

**Interfaces:**
- Consumes: `/api/status` fields (Task 2/4), `/api/speech-device`, `/api/model/delete`, `openSetup(true)` (Task 5).
- Produces: ids `cpuOnly` (checkbox "Always use CPU"), `redownloadModel` ("Re-download…"), `deleteModel` ("Delete
  model", hidden unless `modelOwned`); `pickModel` text becomes "Change folder…".

- [ ] **Step 1: Add the failing assertions** to `tests/test_setup.cjs`:
  - `speechModelDescription` is `Model: Whisper large-v3, running on your NVIDIA GPU.` for `{speechModel: 'Whisper large-v3', speechDevice: 'GPU'}`,
    `Model: Whisper large-v3-turbo, running on the CPU.` for CPU, and `No speech model is set up yet.` when not configured.
  - With `missingModel: 'C:\\models\\large-v3'`: `speechModelDescription` is
    `Your model folder was moved or deleted: C:\models\large-v3. Change folder or re-download.`
  - `speechStatus` is `Local · Whisper large-v3 · GPU`.
  - Toggling `cpuOnly` POSTs `/api/speech-device` with `{cpuOnly: true}`, then refreshes status.
  - `deleteModel.hidden` unless `modelOwned`; clicking it (with `confirm` returning true) POSTs `/api/model/delete`; with `confirm` false, no request.
  - `redownloadModel` opens `setupDialog`, and Start then POSTs `fresh: true`.
  - A transcription reply with `notice` shows it via `notice()`.

  In `tests/test_voice.cjs`, add `speechModel: 'Whisper large-v3', speechDevice: 'GPU'` to the mocked status and
  change both `speechStatus` assertions to `Local · Whisper large-v3 · GPU`.

- [ ] **Step 2: Run** both Node tests — the new assertions fail.

- [ ] **Step 3: Implement.** `updateSpeechStatus` builds the status and description from the status fields; delete asks
  `confirm('Delete the downloaded speech model? You can download it again from Settings.')`.

- [ ] **Step 4: Run** all three test commands — pass.

- [ ] **Step 5: Commit** — `feat: transcription settings for device, re-download and delete`.

---

### Task 7: Real-machine check and spec sync

**Files:**
- Modify: `docs/superpowers/specs/2026-10-20-public-release-design.md` (Decisions "NVIDIA libs (~1 GB)", Part 1
  "NVIDIA libraries", Part 2 "GPU libraries"), `pyproject.toml` (`gpu` extra drops `nvidia-cudnn-cu12==9.*`)

- [ ] **Step 1: Spec and extra.** Replace the cuBLAS + cuDNN wording with: "cuBLAS only (553 MB download, 771 MB on
  disk); CTranslate2 ships its own cuDNN." Drop cuDNN from the `gpu` extra. Run the suite and `test_worker_real`.

- [ ] **Step 2: Isolated window run.** Outside the repo, a launcher sets `ARABIC_RELAY_DATA_DIR` to a new empty temp
  folder, imports `arabic_relay.desktop` and `arabic_relay.server`, sets `server.PORT = 8796` and
  `server.ORIGIN = 'http://127.0.0.1:8796'`, then calls `desktop.main()`. Expected, in order:
  1. The setup dialog opens with the RTX GPU, Codex/Claude/Wispr found states and free space.
  2. Choose `large-v3-turbo` → Download; progress and speed move; Cancel → "Paused…"; Download again resumes (the
     done-bytes don't restart from 0).
  3. The NVIDIA step is skipped (the venv already has cuBLAS); "Testing speech…" ends with "Speech is ready." on GPU.
  4. Settings shows `Whisper large-v3-turbo, running on your NVIDIA GPU`; Always use CPU → status shows CPU; one
     dictation (API on 8796 with a WAV) returns text on CPU; untick it.
  5. Delete model removes the temp `models\large-v3-turbo`; the dialog-less state shows "No speech model is set up yet."
  Close the window; delete the temp folder.

- [ ] **Step 3: cuBLAS download path.** With `ARABIC_RELAY_DATA_DIR` set to a temp folder, run the wheel stage alone:
  `.venv/Scripts/python -c "from arabic_relay import models; w = models.fetch(models.GPU_WHEEL['url'], models.runtime_dir()/'cublas.whl', models.GPU_WHEEL['sha256'], models.GPU_WHEEL['bytes'], print); models.install_gpu_runtime(w)"`
  then confirm `runtime\` holds exactly the two DLLs totalling 771,191,808 bytes, and that a GPU transcription with the
  worker using that folder succeeds. Delete the temp folder.

- [ ] **Step 4: Commit** — `docs: GPU runtime is cuBLAS only; drop cuDNN from the gpu extra`.

---

## Deferred

- Plan 3: translation tool/model/effort dropdowns, login checks, error mapping, Browse… overrides, Wispr Flow registry lookup and **Locate Wispr Flow…**.
- Plan 4: PyInstaller (the `--speech-worker` / `--setup` relaunch of the frozen exe is already handled by
  `paths.self_command`), CI including the Common Voice CC0 Arabic clip test, logs folder and rotation (Plan 2 logs via
  `logging` only), single instance, free port, updates.
- Not planned: keeping the model loaded between recordings (each recording still starts a worker).
