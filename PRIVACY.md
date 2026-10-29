# Privacy

Arabic Relay runs on your computer. This page lists every connection it makes and what it sends.

## What leaves your computer

| Connection | When | What is sent | Where |
|---|---|---|---|
| Codex CLI or Claude Code | You click **Prepare English prompt** or **Improve English spellings** (or turn on automatic spelling correction) | The transcript text | OpenAI or Anthropic, through your own sign-in and plan limits. Never an API key from your environment. |
| Cohere | Only if you choose Cohere for dictation and save your own API key. Off by default. | Your recording | Cohere |
| Hugging Face and PyPI | Once, during setup or when you click Re-download | Nothing personal: the app downloads the Whisper model (Hugging Face) and, with an NVIDIA GPU, NVIDIA's cuBLAS library (PyPI) | huggingface.co, files.pythonhosted.org |
| GitHub Releases | Once a day, unless updates are Off | Nothing personal: the app asks for the latest release and downloads it | github.com |

Sending is always your click. When you send to a ChatGPT or Claude desktop chat, the prompt goes to that app, which
then sends it to its own service under your account. T3 Code delivery stays on your computer (`127.0.0.1`).

Cohere uploads begin only after you stop recording. Recordings longer than two minutes may be split at quiet pauses
and transcribed with at most two requests at once. All audio samples are preserved in order. A failed section fails
the whole transcription; requests are not automatically retried, and the recording remains available in the app.

## What stays on your computer

- Your voice. Local Whisper transcribes recordings in memory; audio is never saved unless you click **Save recording**.
- Settings, send history and the T3 sign-in (encrypted with Windows DPAPI), in `%LOCALAPPDATA%\Arabic Relay`.
- Learned vocabulary and spelling corrections, in the same folder. You can view or clear them in Settings.
- Logs in `%LOCALAPPDATA%\Arabic Relay\logs`; the main log rotates at 1 MB and keeps three files. Logs never contain transcripts or audio.

**History** keeps up to 20 English prompts, send times and destination names locally, one entry per Send action when
at least one destination accepts it. It does not save audio or unsent drafts. Previous accepted sends are imported
once where available. Delete and Clear history remove these history entries; the separate delivery journal used to
prevent duplicate submissions remains until you delete the app's data folder.

No telemetry, analytics or crash reporting.

## Copy diagnostics

**Settings → About → Copy diagnostics** puts a short report on your clipboard for a GitHub issue. It contains the app,
Windows, Python, GPU and driver versions; which speech model and device you use; where Codex, Claude Code and Wispr
Flow were found; the data folder; and the last crash's error trace if there was one. Your Windows user name in paths
is replaced with `<user>`. It never contains transcripts, prompts or audio. Nothing is sent: you choose what to paste.

## Deleting your data

Uninstall Arabic Relay from **Settings → Apps** and answer **Yes** to "Also delete your vocabulary and downloaded
models?". Or delete `%LOCALAPPDATA%\Arabic Relay` yourself.
