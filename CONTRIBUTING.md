# Contributing

## Set up

```powershell
python -m venv .venv
.venv\Scripts\python -m pip install -e .[gpu,build]
```

Python 3.12 or newer on Windows. Leave out `gpu` without an NVIDIA GPU.

## Run

```powershell
.venv\Scripts\python -m arabic_relay            # the desktop window
Start.cmd                                       # in your browser, with data in .local\
```

Set `ARABIC_RELAY_DATA_DIR` to keep a development run away from your real data, and `ARABIC_RELAY_PORT` to use a port
other than 8791.

## Test

```powershell
.venv\Scripts\python -m unittest discover -s tests
node tests\test_voice.cjs
node tests\test_setup.cjs
node tests\test_translation_ui.cjs
node tests\test_ship_ui.cjs
```

The real speech tests need Whisper tiny once:
`python -c "from huggingface_hub import snapshot_download; snapshot_download('Systran/faster-whisper-tiny')"`.
Tests never start the real Codex or Claude Code, never call GitHub, and never run an installer.

## Build

Install [Inno Setup 6](https://jrsoftware.org/isinfo.php) (`winget install --id JRSoftware.InnoSetup -e --scope user`),
then:

```powershell
scripts\build.ps1          # dist\Arabic Relay\ and dist\ArabicRelay-Setup-<version>.exe
scripts\build.ps1 -Smoke   # also install silently, self-test, uninstall (only where Arabic Relay isn't installed)
```

## Never commit personal data

`tests/test_privacy.py` fails on databases, executables, `catalog.json`, personal drive paths, home folders and
key-like strings in tracked files. Use placeholder paths such as `C:\Tools\codex.exe` in tests.
