# Changelog

All notable changes to Arabic Relay. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/) and
versions follow [Semantic Versioning](https://semver.org/).

## [0.2.1] - 2026-10-30

- Prepare English prompt now turns Arabic/English spoken notes into clear, actionable instructions instead of
  translating them sentence by sentence. It organizes the objective, context, and constraints and removes repetition.
- Preserve the user's scope: questions stay questions, requests for ideas stay requests for ideas, and prompt
  preparation is instructed not to invent requirements or additional tasks.
- Update the interface wording to describe prompt preparation. Send delivers the finished prompt to the selected
  sessions without a separate approval step; preparation itself does not send it.

This release carries an Ed25519 update signature verified by existing Arabic Relay installations.
It does not have a Windows Authenticode publisher certificate.

## [0.2.0] - 2026-10-29

- Select existing local Codex and Claude Code sessions directly. Codex receives prompts through its exact-session
  queue; live Claude Code sessions register an encrypted local inbox through Relay's connection hook.
- Remove the browser companion, pairing endpoints, and the obsolete desktop chat automation adapter.
- Keep uncertain submissions from being retried automatically; confirmed direct sends appear in local history.
- Make small interface text more readable.
- Keep the latest 20 sent prompts in local history, with Copy, Use again, and confirmed deletion controls.
- Reduce delays after dictation and during English prompt preparation; retain a clear Cancel recording action.
- Exclude local development databases from packaged application files.

### Connecting existing sessions

- In Settings, use **Direct coding sessions → Connect Codex** or **Connect Claude Code**.
- After connecting Claude Code, reopen existing sessions and refresh Relay. Sessions remain unavailable until
  they register a live local connection. Regular ChatGPT and Claude chats, WSL, SSH, and cloud sessions are excluded.
- Codex accepts prompts into the selected session's queue. Open that session in Codex to process queued prompts.
- Claude Code's permissions still apply. If a send cannot be confirmed, check the destination before sending again.
- Browser companion support has been removed. If you installed the old extension, remove it from Chrome.

This release carries an Ed25519 update signature verified by existing Arabic Relay installations.
It does not have a Windows Authenticode publisher certificate.

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
