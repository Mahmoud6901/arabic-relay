# Wispr Flow quick-capture handoff — 2026-11-01

The compact capture bar moved the editor off-screen (`x ≈ -9962`) while trying to focus it for Wispr Flow. Its mock tests inserted directly into that editor, so they did not cover a usable native dictation target. An existing popup also retained its old provider after the main window selected Flow; the new regression test first failed with “popup kept the old provider.”

The compact bar now contains a visible, focused Flow textarea. Input and composition events feed the existing craft/inline preparation path, which opens review automatically and still requires an explicit Send. The field remains editable during composition and the debounce period. Reopening the popup reads the current saved provider, and switching back to Whisper/Cohere clears the Flow capture state.

Verified with the Flow regression test, existing quick-capture/voice/inline/responsiveness/settings/setup/send UI tests, browser viewport inspection, and the installed native popup. Pasting synthetic mixed Arabic/English text into the native capture field automatically opened the English prompt with recipient selection. No session was sent a message. This checks Relay's insertion and preparation handoff; a live Wispr microphone recording was not performed.

Usage: open Relay's Quick capture with its registered shortcut (Ctrl + Alt + R on this computer), then use Wispr Flow's own shortcut in the focused field. Relay does not intercept Flow dictation into unrelated applications.
