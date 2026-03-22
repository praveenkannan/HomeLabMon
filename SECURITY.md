# Security Policy

## Supported Scope

HomeLabMon is a self-hosted monitoring tool. Security-sensitive areas include:

- API authentication and authorization
- action proposal and confirmation flows
- TLS and listener binding behavior
- secret handling and environment loading
- generated status and incident data exposure

## Reporting a Vulnerability

Do not open public GitHub issues for suspected vulnerabilities.

Instead:

- report privately to the project maintainer through a private channel you already trust, or
- if that is unavailable, open a GitHub security advisory or other non-public disclosure path for the repository

When reporting, include:

- affected version or commit
- reproduction steps
- impact assessment
- any mitigation you have already tested

## Response Expectations

- Acknowledgement target: within 7 days
- Triage target: within 14 days
- Fix timing depends on severity and available maintainer time

## Safe Defaults for Contributors

- never commit secrets or local inventory
- avoid widening network exposure defaults
- preserve explicit confirmation for state-changing actions
- prefer localhost-only or opt-in network access defaults
