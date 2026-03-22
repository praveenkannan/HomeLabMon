# HomeLabMon Setup And Deploy Redesign

## Summary

HomeLabMon should be publishable as a reusable GitHub project without shipping any installation-specific network inventory, target host details, or secrets. The repository should contain only reusable software, schemas, examples, and tooling. Real monitored-device inventory, accepted discovery results, deploy targets, and runtime secrets must remain local to each installation.

The product model should be:

- reusable software in Git
- installation-specific inventory generated locally through an onboarding flow
- generic Linux deployment as the primary release model
- Raspberry Pi as the reference deployment target and the first fully documented path

This redesign also standardizes one reusable public-facing UI behavior: the theme switcher should be a segmented button group with visible System, Light, and Dark options rather than a dropdown.

## Goals

- Keep all personal and network-specific inventory out of tracked repository files.
- Make a fresh clone usable through an onboarding wizard instead of hand-editing private inventory into the repo.
- Support semi-automatic discovery with explicit user approval before any device is monitored.
- Separate setup, build, deploy, and verify into clear stages.
- Make deployment reproducible through versioned release artifacts.
- Keep the software target generic to Linux while documenting Raspberry Pi as the reference host.

## Non-Goals

- Multi-target orchestration across many host classes in the first redesign.
- Fully automatic network profiling without user review.
- Replacing the current Pi runtime architecture in one step.
- Introducing container-first deployment as the primary path.

## Repository Boundary

Tracked in Git:

- runtime code under `bin/`
- dashboard assets under `www/`
- tests under `tests/`
- deploy and setup tooling under `scripts/`
- contracts and product docs under `docs/`
- reusable config schema and example files under `config/`

Local-only and ignored:

- accepted monitored inventory
- raw scan results
- deploy targets
- runtime state
- release apply metadata
- secrets and secret references that point to local files

## Config Model

Tracked files:

- `config/config.schema.json`
- `config/devices.example.json`
- `config/targets.example.json`
- `config/env.example`

Local generated files:

- `config/devices.local.json`
- `config/targets.local.json`
- optional discovery cache under `state/discovery/`

Rules:

- `config/devices.json` should not be a committed source of truth for real installations.
- README and setup docs must point new users to the onboarding wizard, not to an ignored tracked-looking inventory file.
- Example files should describe structure only and should not contain real site inventory.

## Setup Flow

Primary entrypoint:

- `python3 scripts/setup_wizard.py`

Wizard stages:

1. Choose onboarding mode.
   - discovery-assisted
   - manual-only

2. Choose discovery scope.
   - subnet
   - address range
   - manual skip

3. Discover candidates using low-impact checks only.
   - no persistent monitoring yet
   - gather candidate hostname, IP, MAC, and vendor hints when available

4. Review and approve devices.
   - user explicitly accepts or rejects each candidate
   - user may optionally classify role: router, NAS, AP, server, monitor host, other

5. Generate local device inventory.
   - write `config/devices.local.json`
   - include only accepted devices
   - assign conservative default checks

6. Configure deployment targets.
   - write `config/targets.local.json`
   - define one or more Linux targets
   - Raspberry Pi uses the same target model as any other Linux host

7. Generate host env guidance.
   - produce a non-secret host env template
   - secrets remain external to the repo

Manual-only fallback:

- user skips scan entirely
- wizard asks for devices one by one
- output format is identical to discovery-assisted onboarding

## Discovery Policy

Semi-automatic onboarding is the default.

Policy:

- discovery must be opt-in
- no device is monitored automatically
- only approved devices enter the final monitored inventory
- setup must support privacy-restricted environments where scanning is disallowed

The discovery output is advisory. The accepted inventory is the only monitored source of truth.

## Build Strategy

Build should be an explicit release step, separate from setup.

Primary command:

- `python3 scripts/build_release.py --version vX.Y.Z`

Build output:

- `dist/homelabmon-vX.Y.Z.tar.gz`
- manifest with version, file hashes, and build metadata

The build step packages only software and reusable assets. It does not package local accepted inventory, host env files, or runtime state.

## Deploy Strategy

Deployment should target generic Linux first.

Primary command:

- `python3 scripts/deploy_release.py --target <name> --version vX.Y.Z`

Target host layout:

- active root: `/opt/homelabmon`
- versioned releases: `/opt/homelabmon/releases/vX.Y.Z`
- host config: `/etc/homelabmon/`
- mutable runtime state: `/var/lib/homelabmon/`
- logs: `/var/log/homelabmon/`

Deploy behavior:

- copy validated release bundle
- unpack into versioned release directory
- update active pointer or symlink
- preserve host-local config and state
- restart service only after release files pass local verification

Rollback behavior:

- switch active pointer back to previous release
- restart service
- leave config and state untouched

## Raspberry Pi Reference Deployment

Raspberry Pi is the first documented reference target, not a special-case product boundary.

Pi-specific documentation should cover:

- base OS prerequisites
- Python and Node requirements
- systemd service install
- TLS or reverse-proxy path
- Tailscale-only access guidance
- low-power operational guidance

The deploy command should stay the same as generic Linux. The Pi path differs only in docs and target presets.

## Verification Flow

Primary command:

- `python3 scripts/verify_release.py --target <name>`

Verification should include:

- Python compile checks
- dashboard JS syntax checks
- unit tests
- contract verification
- service health verification on target host
- selected host endpoint checks after restart

For Raspberry Pi, docs should explicitly include the existing monitor-cycle and burn-in validation flow.

## Dashboard UX Standard: Theme Control

Replace the theme dropdown with a segmented control.

Required behavior:

- three visible options at all times: System, Light, Dark
- each option is a button with icon plus label
- selected state is obvious without opening a menu
- keyboard navigation remains straightforward
- mobile interaction remains one tap

Why:

- this is a dashboard control, not form input
- current mode should be visible immediately
- segmented controls reduce click depth and match user expectations better than a select menu here

## Migration Plan

1. Remove dependency on tracked real inventory.
2. Add local-only targets and device inventory files to ignore rules and docs.
3. Introduce `setup_wizard.py`.
4. Introduce release build, deploy, and verify scripts.
5. Update README to present onboarding and deployment around the new model.
6. Convert theme selector UI to segmented control.
7. Update Raspberry Pi docs as the reference deployment guide.

## Testing

Add tests for:

- setup wizard output shape
- ignore policy for generated local config files
- release manifest generation
- deploy plan rendering in dry-run mode
- verify flow contract on local and target modes
- segmented theme control rendering and persistence

## Open Questions

- whether discovery should use a bundled probe implementation or shell out to existing system tools for the first version
- whether release artifacts should be tarball-only or include a plain directory manifest for simpler dry runs
- whether target configuration should support multiple named hosts from day one or focus on one default target plus future extension
