# Security Policy

## Supported versions

| Version | Supported |
|---------|-----------|
| 0.4.x   | Yes       |
| < 0.4   | No        |

## Reporting a vulnerability

Do not open a public GitHub issue for security vulnerabilities.

Report vulnerabilities privately by emailing the maintainer or using GitHub's private vulnerability reporting feature (Security > Report a vulnerability on the repo page).

Include:
- A description of the vulnerability
- Steps to reproduce it
- The version of pyupcheck affected
- Any potential impact you can identify

You will receive a response within 72 hours. If the vulnerability is confirmed, a patched release will be published and you will be credited in the changelog unless you prefer to remain anonymous.

## Scope

pyupcheck is a local CLI tool. It downloads package metadata from PyPI and GitHub over HTTPS. It does not transmit your code or project data to any external service. The only outbound requests it makes are to:

- `https://pypi.org/pypi/` — package metadata
- `https://raw.githubusercontent.com/` — changelog files
- `https://api.github.com/` — release data (unauthenticated or with user-supplied token)

If you discover that pyupcheck is making unexpected outbound requests or handling user data in a way that creates a security risk, that is in scope.
