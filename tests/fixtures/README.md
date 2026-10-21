# Test fixtures

## `arabic_fleurs.wav`

Six seconds of Egyptian Arabic speech for the real speech-worker test on the CPU.

- Source: [Google FLEURS](https://huggingface.co/datasets/google/fleurs), config `ar_eg`, split `test`, row `id` 1778.
- License: CC-BY-4.0 (Google). Converted to 16 kHz mono 16-bit PCM; otherwise unchanged.
- Transcription: يتصاعد من المصنع دخاناً أبيض، وهذا ما أظهرته تقارير تلفزيونية.

## `codex_models.json`

Trimmed real output of `codex debug models` (2026-10-20), used by `tests/test_translators.py`.
