# Contributing to HomeLabMon

HomeLabMon is intended to stay reusable across Linux installations while keeping device inventory, secrets, and runtime state local to each deployment.

## Ground Rules

- Do not commit secrets, local inventory, or runtime state.
- Treat `config/devices.example.json` as documentation, not as a real installation inventory.
- Keep behavior changes covered by tests.
- Prefer minimal, contract-preserving changes over broad refactors.

## Local Development

HomeLabMon is stdlib-first today. There is no `pyproject.toml` or pinned dependency file in the repo.

Common verification commands:

```bash
python3 -m unittest tests.test_config_contract tests.test_history tests.test_dashboard_selected_panel tests.test_monitor tests.test_send_alert tests.test_temperature_collect tests.test_ai_gateway tests.test_check_devices_temperature
python3 -m py_compile bin/*.py
node --check www/status.js
bash scripts/setup-init.sh --dry-run
bash scripts/setup-apply.sh --dry-run
bash scripts/setup-verify.sh --dry-run
```

## Configuration and Privacy

- Keep installation-specific inventory in `config/devices.local.json` or an external path set through `HOMELABMON_CONFIG_PATH`.
- Keep secrets outside the repo, typically via `/etc/homelabmon/monitor.env` or another deployment-specific secret source.
- Do not add personal domains, overlay-network hostnames, or local service URLs to tracked examples or docs.

## Pull Requests

- Explain the user-facing or operator-facing problem.
- List the files changed and why.
- Include verification commands and results.
- Call out any follow-up work you intentionally left out.
