# Temperature And Heat Contract

HomeLabMon v1 exposes a single device heat state derived from global defaults plus optional per-device overrides.

## States

- `NORMAL`: reading is below `warm_c`
- `WARM`: reading is greater than or equal to `warm_c` and below `hot_c`
- `HOT`: reading is greater than or equal to `hot_c`
- `UNKNOWN`: no usable reading is available

`UNKNOWN` applies when the sample is missing, unparsable, non-numeric, or older than `stale_after_s`.

## Threshold Model

Effective thresholds are resolved in this order:

1. Per-device override
2. Global default

Required invariants:

- `warm_c < hot_c`
- `stale_after_s >= 30`

Recommended v1 defaults:

```json
{
  "temperature": {
    "defaults": {
      "warm_c": 60,
      "hot_c": 75,
      "stale_after_s": 300
    },
    "devices": {
      "router": {
        "warm_c": 55,
        "hot_c": 70
      }
    }
  }
}
```

## Evaluation Rules

- Use the most recent valid sample for each sensor.
- If a device has multiple sensors, the device heat state is the highest non-`UNKNOWN` sensor state.
- If all sensors are `UNKNOWN`, the device heat state is `UNKNOWN`.
- Threshold comparison is inclusive at the boundary:
  - `value_c >= warm_c` enters `WARM`
  - `value_c >= hot_c` enters `HOT`

## API Shape

Device payloads use:

```json
{
  "heat": {
    "state": "WARM",
    "value_c": 63.5,
    "sampled_at": "2026-03-21T20:17:42Z",
    "thresholds": {
      "warm_c": 60,
      "hot_c": 75,
      "source": "global"
    }
  }
}
```

Field rules:

- `value_c` is nullable when `state` is `UNKNOWN`
- `sampled_at` is nullable when `state` is `UNKNOWN`
- `thresholds.source` is `global` or `device`

## Incident Interaction

- `NORMAL`: no temperature incident
- `WARM`: advisory only in v1; show in UI, do not open incident
- `HOT`: eligible for incident creation using the standard incident open and resolve rules with `severity: critical`
- `UNKNOWN`: keep any existing temperature incident unchanged until a valid sample arrives
