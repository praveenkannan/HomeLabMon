# Temperature Telemetry Setup

`bin/temperature_collect.py` is the Pi-safe collector for per-device heat telemetry. It uses only the Python standard library and prefers existing system tools over Python SNMP packages.

## Config Shape

Add a top-level `temperature` block to `config/devices.json`:

```json
{
  "temperature": {
    "defaults": {
      "warning_c": 60.0,
      "critical_c": 75.0,
      "stale_after_s": 300,
      "command_timeout_s": 3.0,
      "http_timeout_s": 2.0
    },
    "devices": {
      "synology-nas": {
        "method": "snmpv3",
        "oid": "1.3.6.1.4.1.6574.1.2.0",
        "username_env": "HLM_SYNOLOGY_SNMP_USER",
        "auth_password_env": "HLM_SYNOLOGY_SNMP_AUTH",
        "privacy_password_env": "HLM_SYNOLOGY_SNMP_PRIV"
      }
    }
  }
}
```

Per-device overrides live in `temperature.devices.<device-name>`. Supported methods:

- `snmpv3`: SNMPv3 via the host `snmpget` binary
- `mac_api`: lightweight JSON endpoint on the Mac Studio
- `local_file`: local sysfs or hwmon temperature file on the Pi

## Capability States

Each device is classified into one of three states:

- `supported`: a temperature method is configured and collection succeeded
- `unsupported`: no temperature method is configured for the device
- `unavailable`: the method is configured, but the collector cannot currently use it

Typical `unavailable` reasons:

- `snmpget` is not installed
- a required SNMPv3 environment variable is missing
- the configured SNMP OID does not respond
- the Mac exporter URL is down or returns invalid JSON
- a local file sensor path cannot be read

Stale samples keep `capability: supported` but return `heat.state: UNKNOWN`.

## SNMPv3 Notes

HomeLabMon is SNMPv3-first for `synology-nas` and `asus-router`.

Synology:

- Default sample OID in `config/devices.json`: `1.3.6.1.4.1.6574.1.2.0`
- Install Net-SNMP tools on the Pi so `snmpget` is available.

ASUS:

- ASUS firmware and MIB exposure vary. Replace `REPLACE_ME_ASUS_TEMP_OID` with the actual temperature OID exposed by your router.
- Keep the router on SNMPv3 if the firmware supports it. Do not downgrade to SNMPv2c just to make the collector work.

Required environment variables:

- `HLM_SYNOLOGY_SNMP_USER`
- `HLM_SYNOLOGY_SNMP_AUTH`
- `HLM_SYNOLOGY_SNMP_PRIV`
- `HLM_ASUS_SNMP_USER`
- `HLM_ASUS_SNMP_AUTH`
- `HLM_ASUS_SNMP_PRIV`

If a target only supports `authNoPriv`, keep `privacy_password_env` unset in config and set `security_level` to `authNoPriv`.

## Mac Studio Hook

The optional `mac_api` method expects a small JSON payload such as:

```json
{
  "temperature_c": 62.4,
  "sampled_at": "2026-03-22T02:58:00+00:00"
}
```

The default URL in repo config is a placeholder:

- `http://REPLACE_ME_MACSTUDIO_IP:9100/temperature`

Replace it with the Mac Studio exporter or API endpoint when that piece exists.

## CLI Usage

Collect all configured temperature-capable devices:

```bash
python3 bin/temperature_collect.py
```

Collect a single device:

```bash
python3 bin/temperature_collect.py --device synology-nas
```

## `check_devices.py` Integration Contract

This worker slice does not modify `check_devices.py`. The intended integration is:

1. Import `collect_temperature_inventory` from `bin/temperature_collect.py`.
2. Load the shared `config/devices.json` once and pass the same config into both collectors.
3. Merge `payload["devices"][name]` into each device state under a new key such as `temperature`.
4. Use `heat.state == "HOT"` to emit incident evaluations with `check_key` like `temperature:system` and `reason_code: threshold_breach`.
5. Treat `WARM` as advisory only and keep `UNKNOWN` from opening or resolving incidents, matching the contract docs.
