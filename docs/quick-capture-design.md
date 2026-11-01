# Shortcut review design

The shortcut asks: is this the prompt I want to send, and to which sessions?

- Objects: one editable English prompt (up to 16,000 characters), source recognition, warnings, and up to 20 selected sessions.
- Sessions come from the live catalog; no sample destinations ship with this change.
- Frequent job: review, choose recipients, then explicitly send the exact prompt.
- Other jobs: copy, correct recognition, retry preparation, record again, dismiss.
- Recording and preparation remain separate states; no automatic sending.
- The prompt is the largest element; source details remain reachable in the scrolling editor.
- The delivery dock never shrinks into the editor. Both delivery buttons are at least 48px high.
- The picker uses the entire popup, with fixed search and Done controls above its scrolling list.

## Direction

Design-taste-frontend: true-black-flat, seed 597237455; palette and type retained from Relay.
No redraw. The compact window uses the narrow-screen full-sheet picker instead of a permanent sidebar.
Face: Relay's Instrument Serif heading, existing body face, neutral surfaces and cyan signals.
Signature: the reviewed voice prompt above its selected session destinations.
Light mode inherits the same app tokens rather than forcing a dark popup.

## Verification

Browser inspection at 620x560, 620x400 and 390x560: review, blank draft, source controls, search, selection and return to review. No horizontal overflow at 390px; recipient control measured 48px. Light and dark themes checked. No prompts sent to real sessions.
Native hotkey tests, shortcut UI tests, inline workflow tests, shortcut voice tests and translation settings tests pass.
Scoped popup stylesheet lint: zero failures or warnings. Whole-file lint also reports pre-existing legacy gradients, monospace and motion declarations outside this redesign; those are not introduced by this change. Browser inspection used the approved computer-use interface instead of the skill's standalone browser driver.
