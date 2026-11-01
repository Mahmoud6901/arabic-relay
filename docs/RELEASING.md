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

Releases through v0.3.0 have Ed25519 update signatures but no Windows Authenticode publisher signature.
The SignPath Foundation application is being prepared. See the [Code signing policy](../CODE_SIGNING.md).
Do not describe Foundation signing as active until approval, artifact review, and workflow integration are complete.
The application executable needs product/version metadata, and the bundled NVIDIA cuDNN and Microsoft WebView2
bootstrapper need an eligibility decision. After activation, Authenticode-sign and timestamp the application and
installer before producing SHA256SUMS.txt and the Ed25519 update signature.
