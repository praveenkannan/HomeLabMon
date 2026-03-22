#!/usr/bin/env bash
set -u

SERVICE_NAME="pi-monitor-status.service"
UNIT_FILE="/etc/systemd/system/pi-monitor-status.service"
SSHD_CONFIG="/etc/ssh/sshd_config"
EXPECT_BIND_HOST="127.0.0.1"
EXPECT_BIND_PORT="8081"
SYSTEMCTL_ENV_FILE=""
LISTENERS_FILE=""
UFW_STATUS_FILE=""
SSH_ALLOW_INTERFACE=""
OVERLAY_UDP_PORT=""
MAINTENANCE_CIDR=""

PASS_COUNT=0
WARN_COUNT=0
FAIL_COUNT=0

usage() {
  cat <<'EOF'
Usage: security_verify.sh [options]

Read-only security posture checks for HomeLabMon on Pi.

Options:
  --service-name NAME         systemd service name (default: pi-monitor-status.service)
  --unit-file PATH            systemd unit file path (default: /etc/systemd/system/pi-monitor-status.service)
  --sshd-config PATH          sshd_config path (default: /etc/ssh/sshd_config)
  --expect-bind-host HOST     expected local bind host (default: 127.0.0.1)
  --expect-bind-port PORT     expected dashboard port (default: 8081)
  --systemctl-env-file PATH   use captured `systemctl show ... Environment` output
  --listeners-file PATH       use captured `ss -lnt` output
  --ufw-status-file PATH      use captured `ufw status verbose` output
  --ssh-allow-interface NAME  require ssh allow rule on a specific interface
  --overlay-udp-port PORT     require overlay UDP allow rule for a specific port
  --maintenance-cidr CIDR     check for an optional LAN maintenance ssh rule
  --help                      show help
EOF
}

pass() {
  PASS_COUNT=$((PASS_COUNT + 1))
  printf 'PASS  %s\n' "$1"
}

warn() {
  WARN_COUNT=$((WARN_COUNT + 1))
  printf 'WARN  %s\n' "$1"
}

fail() {
  FAIL_COUNT=$((FAIL_COUNT + 1))
  printf 'FAIL  %s\n' "$1"
}

parse_args() {
  while [[ $# -gt 0 ]]; do
    case "$1" in
      --service-name)
        SERVICE_NAME="$2"
        shift 2
        ;;
      --unit-file)
        UNIT_FILE="$2"
        shift 2
        ;;
      --sshd-config)
        SSHD_CONFIG="$2"
        shift 2
        ;;
      --expect-bind-host)
        EXPECT_BIND_HOST="$2"
        shift 2
        ;;
      --expect-bind-port)
        EXPECT_BIND_PORT="$2"
        shift 2
        ;;
      --systemctl-env-file)
        SYSTEMCTL_ENV_FILE="$2"
        shift 2
        ;;
      --listeners-file)
        LISTENERS_FILE="$2"
        shift 2
        ;;
      --ufw-status-file)
        UFW_STATUS_FILE="$2"
        shift 2
        ;;
      --ssh-allow-interface)
        SSH_ALLOW_INTERFACE="$2"
        shift 2
        ;;
      --overlay-udp-port)
        OVERLAY_UDP_PORT="$2"
        shift 2
        ;;
      --maintenance-cidr)
        MAINTENANCE_CIDR="$2"
        shift 2
        ;;
      --help|-h)
        usage
        exit 0
        ;;
      *)
        echo "Unknown argument: $1" >&2
        usage >&2
        exit 2
        ;;
    esac
  done
}

read_or_empty() {
  local path="$1"
  if [[ -n "$path" && -r "$path" ]]; then
    cat "$path"
  fi
}

extract_bind_from_env_blob() {
  local blob="$1"
  local token=""
  token=$(printf '%s' "$blob" | tr ' ' '\n' | grep -E '^PI_MONITOR_BIND_HOST=' | tail -n 1 || true)
  token="${token#PI_MONITOR_BIND_HOST=}"
  token="${token%\"}"
  token="${token#\"}"
  printf '%s' "$token"
}

extract_bind_from_unit_line() {
  local line="$1"
  local token="${line#*PI_MONITOR_BIND_HOST=}"
  token="${token%%[[:space:]]*}"
  token="${token%\"}"
  token="${token#\"}"
  printf '%s' "$token"
}

load_sshd_effective() {
  local key="$1"
  local value=""
  if command -v sshd >/dev/null 2>&1; then
    value=$(sshd -T -f "$SSHD_CONFIG" 2>/dev/null | awk -v k="$key" '$1 == k {print tolower($2); exit}' || true)
  fi
  if [[ -n "$value" ]]; then
    printf '%s' "$value"
    return
  fi
  if [[ -r "$SSHD_CONFIG" ]]; then
    value=$(awk -v k="$key" '
      /^[[:space:]]*#/ { next }
      NF < 2 { next }
      tolower($1) == k { v = tolower($2) }
      END { if (v != "") print v }
    ' "$SSHD_CONFIG")
  fi
  printf '%s' "$value"
}

check_bind_policy() {
  local env_blob=""
  local env_bind=""
  local unit_line=""
  local unit_bind=""

  env_blob=$(read_or_empty "$SYSTEMCTL_ENV_FILE")
  if [[ -z "$env_blob" ]] && command -v systemctl >/dev/null 2>&1; then
    env_blob=$(systemctl show "$SERVICE_NAME" --property=Environment --value 2>/dev/null || true)
  fi

  if [[ -n "$env_blob" ]]; then
    env_bind=$(extract_bind_from_env_blob "$env_blob")
    if [[ "$env_bind" == "$EXPECT_BIND_HOST" ]]; then
      pass "systemd env enforces PI_MONITOR_BIND_HOST=$EXPECT_BIND_HOST"
    elif [[ -n "$env_bind" ]]; then
      fail "systemd env bind host is $env_bind (expected $EXPECT_BIND_HOST)"
    else
      warn "systemd env does not expose PI_MONITOR_BIND_HOST; checking unit file"
    fi
  else
    warn "unable to read systemd environment for $SERVICE_NAME"
  fi

  if [[ -r "$UNIT_FILE" ]]; then
    unit_line=$(grep -E '^[[:space:]]*Environment=PI_MONITOR_BIND_HOST=' "$UNIT_FILE" | tail -n 1 || true)
    if [[ -n "$unit_line" ]]; then
      unit_bind=$(extract_bind_from_unit_line "$unit_line")
      if [[ "$unit_bind" == "$EXPECT_BIND_HOST" ]]; then
        pass "unit file bind host is $EXPECT_BIND_HOST"
      else
        fail "unit file bind host is $unit_bind (expected $EXPECT_BIND_HOST)"
      fi
    else
      warn "unit file does not set PI_MONITOR_BIND_HOST explicitly"
    fi
  else
    fail "missing unreadable unit file: $UNIT_FILE"
  fi
}

check_listener_binding() {
  local listeners=""
  local local_addrs=()
  local line=""
  local addr=""
  local host=""
  local unsafe=0
  local seen=0

  listeners=$(read_or_empty "$LISTENERS_FILE")
  if [[ -z "$listeners" ]] && command -v ss >/dev/null 2>&1; then
    listeners=$(ss -lnt 2>/dev/null || true)
  fi

  if [[ -z "$listeners" ]]; then
    warn "unable to inspect active listeners (missing ss output)"
    return
  fi

  while IFS= read -r line; do
    case "$line" in
      *":${EXPECT_BIND_PORT}"*)
        addr=$(printf '%s' "$line" | awk '{print $4}')
        if [[ -n "$addr" ]]; then
          local_addrs+=("$addr")
          seen=1
        fi
        ;;
    esac
  done <<< "$listeners"

  if [[ "$seen" -eq 0 ]]; then
    warn "no TCP listener found on port $EXPECT_BIND_PORT (service may be down)"
    return
  fi

  for addr in "${local_addrs[@]}"; do
    if [[ "$addr" == \[*\]:"$EXPECT_BIND_PORT" ]]; then
      host="${addr%%]:*}"
      host="${host#[}"
    else
      host="${addr%:*}"
    fi

    if [[ "$host" == "$EXPECT_BIND_HOST" || "$host" == "127.0.0.1" || "$host" == "::1" ]]; then
      continue
    fi
    unsafe=1
    fail "listener exposes port $EXPECT_BIND_PORT on $host (expected loopback only)"
  done

  if [[ "$unsafe" -eq 0 ]]; then
    pass "listeners on port $EXPECT_BIND_PORT are loopback-only"
  fi
}

check_ssh_posture() {
  local pa=""
  local kia=""
  local prl=""

  if [[ ! -r "$SSHD_CONFIG" ]]; then
    fail "missing unreadable ssh config: $SSHD_CONFIG"
    return
  fi

  pa=$(load_sshd_effective "passwordauthentication")
  kia=$(load_sshd_effective "kbdinteractiveauthentication")
  prl=$(load_sshd_effective "permitrootlogin")

  if [[ "$pa" == "no" ]]; then
    pass "ssh password authentication disabled"
  else
    fail "ssh PasswordAuthentication is '$pa' (expected no)"
  fi

  if [[ "$kia" == "no" ]]; then
    pass "ssh keyboard-interactive authentication disabled"
  else
    fail "ssh KbdInteractiveAuthentication is '$kia' (expected no)"
  fi

  if [[ "$prl" == "no" ]]; then
    pass "ssh root login disabled"
  else
    fail "ssh PermitRootLogin is '$prl' (expected no)"
  fi
}

check_firewall() {
  local ufw_blob=""
  ufw_blob=$(read_or_empty "$UFW_STATUS_FILE")
  if [[ -z "$ufw_blob" ]] && command -v ufw >/dev/null 2>&1; then
    ufw_blob=$(ufw status verbose 2>/dev/null || true)
  fi

  if [[ -z "$ufw_blob" ]]; then
    warn "unable to inspect ufw status"
    return
  fi

  if printf '%s\n' "$ufw_blob" | grep -qi '^Status:[[:space:]]*active'; then
    pass "ufw is active"
  else
    fail "ufw is not active"
  fi

  if printf '%s\n' "$ufw_blob" | grep -qiE 'Default:.*deny \(incoming\)'; then
    pass "ufw default incoming policy is deny"
  else
    fail "ufw default incoming policy is not deny"
  fi

  if printf '%s\n' "$ufw_blob" | grep -qiE '8081(/tcp)?[[:space:]].*DENY'; then
    pass "firewall denies dashboard direct access on 8081/tcp"
  else
    fail "missing deny rule for 8081/tcp"
  fi

  if [[ -n "$SSH_ALLOW_INTERFACE" ]]; then
    if printf '%s\n' "$ufw_blob" | grep -qiE "22/tcp.*(${SSH_ALLOW_INTERFACE}.*ALLOW IN|ALLOW IN.*${SSH_ALLOW_INTERFACE})"; then
      pass "ssh allow rule limited to ${SSH_ALLOW_INTERFACE}"
    else
      fail "missing ssh allow rule on ${SSH_ALLOW_INTERFACE}"
    fi
  else
    warn "ssh interface allow rule not checked (no --ssh-allow-interface provided)"
  fi

  if [[ -n "$OVERLAY_UDP_PORT" ]]; then
    if printf '%s\n' "$ufw_blob" | grep -qiE "${OVERLAY_UDP_PORT}/udp[[:space:]].*ALLOW IN"; then
      pass "overlay UDP ${OVERLAY_UDP_PORT} allowed"
    else
      warn "missing explicit allow for ${OVERLAY_UDP_PORT}/udp"
    fi
  else
    warn "overlay UDP allow rule not checked (no --overlay-udp-port provided)"
  fi

  if [[ -n "$MAINTENANCE_CIDR" ]]; then
    if printf '%s\n' "$ufw_blob" | grep -qiE "22/tcp.*(${MAINTENANCE_CIDR}.*ALLOW IN|ALLOW IN.*${MAINTENANCE_CIDR})"; then
      pass "optional LAN maintenance rule for ssh exists"
    else
      warn "LAN maintenance ssh allow rule not found for ${MAINTENANCE_CIDR}"
    fi
  else
    warn "LAN maintenance ssh rule not checked (no --maintenance-cidr provided)"
  fi
}

main() {
  parse_args "$@"
  echo "HomeLabMon security verification"
  echo "service=$SERVICE_NAME expect_bind=${EXPECT_BIND_HOST}:${EXPECT_BIND_PORT}"

  check_bind_policy
  check_listener_binding
  check_ssh_posture
  check_firewall

  echo
  echo "Summary: pass=$PASS_COUNT warn=$WARN_COUNT fail=$FAIL_COUNT"
  if [[ "$FAIL_COUNT" -gt 0 ]]; then
    return 1
  fi
  return 0
}

main "$@"
