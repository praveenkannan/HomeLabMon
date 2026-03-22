# HomeLabMon

HomeLabMon is a self-hosted home network monitor for Linux hosts that gives you a live dashboard for every device on your network - routers, NAS, APs, servers - with ping/SNMP health checks, temperature telemetry, incident alerts, and an optional AI assistant.

No cloud required. The repo keeps reusable code, schemas, examples, and scripts only. Local device inventory and runtime state are created per installation and stay out of Git.

The codebase is stdlib-first today: there is no `pyproject.toml` or pinned Python requirements file in the repo.

## Highlights

- local-first device inventory and runtime state
- contract-driven config and API docs
- low-footprint Python runtime with JSON read models
- optional AI mode that degrades safely when no key is configured
- action proposal plus confirmation flow for state-changing operations
- desktop and mobile dashboard views with selected-device detail

## Repository Layout

- `bin/`: runtime Python modules and shell helpers
- `www/`: static dashboard assets
- `config/`: config schema, examples, and environment templates
- `state/`: runtime state and release manifests (local-only, not committed)
- `scripts/`: current setup, deploy, verify, and rollback entrypoints
- `docs/`: design docs and API/incident contract documents
- `tests/`: unit and smoke tests for runtime and contract behavior
- `.github/`: CI and contributor workflow templates

## Configuration

The v1 config contract covers:

- instance identity: `instance_name`, `site_title`, `service_name`, `dashboard_public_url`, `timezone`
- contract versioning: `api_contract_version`
- feature flags: AI chat, animation, and restart-action toggles
- AI settings: enablement mode, fallback policy, provider, base URL, model, and timeout
- device inventory: host identity, probe definitions, action policy, and maintenance state

See `config/config.schema.json` for the full schema and `config/devices.example.json` for the canonical example shape. `scripts/setup-init.sh` can create a local `config/devices.local.json` from that example when you want a starting point, but the repo does not treat local device inventory as a committed source of truth.

## Secret Policy

- Keep all secrets out of Git.
- Track only non-secret defaults in `config/env.example`.
- Inject runtime secrets through an external environment file.
- Store file-backed secrets outside the repo.
- Do not commit `.env`, `config/devices.json`, `config/devices.local.json`, or anything under `state/`.
- Do not commit generated dashboard output such as `www/status.html`.

## Quickstart

```bash
# Initialize the local repo skeleton and example config
bash scripts/setup-init.sh

# Preview the local setup apply step without mutating files
bash scripts/setup-apply.sh --dry-run

# Validate the checked-in config and script layout
bash scripts/setup-verify.sh --dry-run

# Deploy the current release placeholder
bash scripts/deploy.sh --dry-run

# Roll back to the previous release placeholder
bash scripts/rollback.sh --dry-run
```

Run the test suite locally:

```bash
python3 -m unittest tests.test_history tests.test_monitor tests.test_send_alert tests.test_temperature_collect tests.test_ai_gateway tests.test_check_devices_temperature
python3 -m py_compile bin/*.py
node --check www/status.js
```

## Lifecycle

The current shell scripts cover the basic lifecycle:

- `scripts/setup-init.sh`: create local directories and copy the example device config into `config/devices.local.json` if needed
- `scripts/setup-apply.sh`: record a local apply manifest after validating inputs
- `scripts/setup-verify.sh`: verify the checked-in schema, examples, and script layout
- `scripts/deploy.sh`: write a release placeholder and update the active pointer
- `scripts/rollback.sh`: switch the active pointer back to the previous release placeholder

## GitHub Project Surface

- [LICENSE](./LICENSE): MIT
- [CONTRIBUTING.md](./CONTRIBUTING.md): contributor workflow and verification expectations
- [SECURITY.md](./SECURITY.md): private vulnerability reporting guidance
- [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md): collaboration baseline
- `.github/workflows/ci.yml`: runs the same verification commands used locally

## Current Limitations

- setup and deploy scripts are still shell-based placeholders, not full release packaging
- the runtime layout is portable across Linux installs, but deployment docs are still maturing
- the repository ships examples and contracts, not a real inventory discovery wizard yet
