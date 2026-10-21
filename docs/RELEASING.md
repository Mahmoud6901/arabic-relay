# Releasing

## Before tagging

1. Bump `__version__` in `src/arabic_relay/__init__.py`.
2. Add a `## [<version>] - <date>` section to `CHANGELOG.md`; it becomes the release notes.
3. Run every test command in `CONTRIBUTING.md`, then `scripts\build.ps1`. (The Release workflow runs the installer
   smoke test; the smoke option refuses to run on a computer where Arabic Relay is installed.)
4. Manual checks:
   - Dictate on a real NVIDIA GPU and on the CPU (Always use CPU).
   - First launch on a clean machine: Windows Sandbox (Pro/Enterprise) or Microsoft's free Windows VM image (Home).
   - Update from the previous release; learned vocabulary survives.

## Tag and sign

```powershell
git tag v<version>
git push origin v<version>
```

The Release workflow tests, builds, smoke-tests the installer and opens a **draft** release with the installer and
`SHA256SUMS.txt`. Then, on the owner's computer:

```powershell
.venv\Scripts\python scripts\sign_release.py v<version>
```

It downloads the draft's installer, signs it with the offline key, checks the signature against the public key built
into the app, uploads `<installer>.sig` and publishes the release. Installed apps pick it up within a day.

## The signing key

- Lives at `%USERPROFILE%\.arabic-relay\release-signing.key` and never leaves that computer.
- Back it up (for example in a password manager). If it is lost, make a new one with
  `python scripts\sign_release.py --new-key`, put the new public key in `updates.PUBLIC_KEY`, and tell users to
  reinstall manually once: installed apps only trust the old key.

## Code signing

v0.1 ships unsigned. Once SignPath Foundation approves the project, Authenticode signing becomes one more workflow
step, and updates also require the same publisher as the running app.
