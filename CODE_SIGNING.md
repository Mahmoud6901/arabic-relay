# Code signing policy

## Current status

Arabic Relay's application to SignPath Foundation was submitted on 1 November 2026. Approval is pending.
Windows Authenticode signing is not yet enabled. Releases through v0.3.0 carry the project's Ed25519 update signature, which Relay verifies;
this is separate from a Windows publisher certificate.

If the application is approved, the intended attribution is:
“Free code signing provided by [SignPath.io](https://about.signpath.io), certificate by
[SignPath Foundation](https://signpath.org).” This is not a claim of current sponsorship or approval.

## Responsibilities

- Author and reviewer: [Mahmoud Adel Hassan (@Mahmoud6901)](https://github.com/Mahmoud6901).
- Release signing approver: [Mahmoud Adel Hassan (@Mahmoud6901)](https://github.com/Mahmoud6901).

Before SignPath signing is activated, maintainers must enable multi-factor authentication for GitHub and SignPath.
External contributions require maintainer review. Each Foundation signing request requires human approval.

## Scope and release checks

Signing is intended for Arabic Relay's application executable and Windows installer, built from this repository
by the [Release workflow](.github/workflows/release.yml). Third-party binaries must retain their own signatures;
they will not be re-signed as Relay's code. Product metadata and the package contents must pass SignPath's review
before this integration is enabled. The current package includes NVIDIA cuDNN and a Microsoft WebView2 bootstrapper;
their eligibility requires clarification with SignPath.

Releases must pass the automated tests and installer smoke test. Once enabled, Windows signing and timestamping
will precede generation of checksums and the existing Ed25519 update signature.

## Privacy

See [PRIVACY.md](PRIVACY.md) for local storage, optional online speech and AI services, updates, and user controls.
Signing does not change where user audio or text is processed.
