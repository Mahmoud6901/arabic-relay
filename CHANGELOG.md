# Changelog

All notable changes to Arabic Relay. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versions follow [Semantic Versioning](https://semver.org/).

## [0.1.0] - 2026-10-21

First public release.

### Added

- Windows installer for your user only, no admin rights; automatic updates verified with an Ed25519 signature.
- First-launch setup: checks your GPU, tools and disk space, then downloads Whisper large-v3 or large-v3-turbo with
  progress, Cancel and resume. NVIDIA cuBLAS is downloaded only when an NVIDIA GPU is found.
- Local Arabic and English dictation on the GPU or CPU, with automatic fallback to the CPU. Wispr Flow and Cohere
  remain optional alternatives.
- English prompts with Codex CLI or Claude Code: choose the tool, model and effort in Settings.
- Finds Codex, Claude Code, Wispr Flow and existing Whisper models in custom install folders, with Browse… buttons.
- One window at a time; a busy port falls back to a free one.
- Logs, crash capture and Copy diagnostics for bug reports.
