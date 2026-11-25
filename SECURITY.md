# Security Policy

## Supported Versions

Relikt is a small open-source extraction toolkit. Security fixes are provided on a best-effort basis for:

| Version | Supported |
| --- | --- |
| Latest `main` branch | Yes |
| Older commits / forks | No |

If you are using a fork or an older snapshot, please reproduce the issue on the latest `main` branch before reporting it.

## Reporting a Vulnerability

Please **do not** open a public GitHub issue for a suspected security vulnerability.

Preferred reporting path:

1. Use GitHub's **Private Vulnerability Reporting** / **Security Advisories** for this repository, if it is enabled.
2. If private reporting is not enabled, open a minimal public issue that does **not** include exploit details and ask the maintainers for a private reporting channel.

When reporting, include as much of the following as you can:

- A clear description of the issue and its impact.
- Affected file(s), command(s), or workflow.
- Steps to reproduce, or a proof of concept that is safe to share privately.
- The environment you used (OS, Python version, relevant command).
- Any suggested mitigation or fix, if you have one.

You can expect an initial best-effort acknowledgement after the report is triaged. Timing for fixes and disclosure may vary depending on severity, maintainer availability, and whether the issue affects this repository or an upstream dependency.

## Security Considerations for This Project

Relikt processes legacy binary artifacts and dynamically loads edition-specific extractor modules. Please keep these project-specific risks in mind:

- **Untrusted extractor code:** Modules under `tools/<edition>/` are imported and executed by `relikt.py`, including during `verify`. Review new or modified extractor modules before running them, especially in forks or pull requests.
- **Untrusted input binaries:** Files placed in `data/<edition>/` are treated as source artifacts for analysis/extraction. Only use binaries you trust and understand, and prefer running unfamiliar inputs in an isolated environment.
- **Generated output:** `out/` and `tmp/` may contain extracted content or diagnostics. Do not commit generated content, and treat extracted files as untrusted before opening them with other tools.
- **No secrets in repository:** Do not commit passwords, API keys, tokens, private URLs, or other sensitive data. Reports and pull requests should also avoid including secrets.
- **Supply chain:** The runtime extractors are intended to use Python 3.10+ standard library only. Optional external tools mentioned in documentation (such as DOSBox-X, REA, Rizin, deark) are for manual analysis and should be obtained from their official sources.

## Disclosure

Please allow maintainers reasonable time to investigate and address a confirmed vulnerability before public disclosure. Once a fix or mitigation is available, disclosure should include:

- The affected component or workflow.
- The impact and severity, if known.
- Mitigation steps or upgrade/patch guidance.
- Credit to the reporter, if they want it.

## Out of Scope

The following are generally not considered security vulnerabilities in this repository:

- Issues caused only by running untrusted third-party extractor code that a user explicitly added.
- Malicious behavior contained inside user-supplied magazine binaries themselves, except where Relikt handles them unsafely.
- Copyright or licensing concerns unrelated to security. Please report those through normal project contribution channels.