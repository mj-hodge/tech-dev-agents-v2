#!/usr/bin/env bash
set -u

ts() {
  date -u '+%Y-%m-%dT%H:%M:%SZ'
}

log() {
  printf '[%s] %s\n' "$(ts)" "$*"
}

emit_status() {
  printf 'STATUS=%s\n' "$1"
}

pass() {
  emit_status PASS
  exit 0
}

fail() {
  printf 'DETAIL=%s\n' "$1"
  emit_status FAIL
  exit 1
}

blocked() {
  printf 'DETAIL=%s\n' "$1"
  emit_status BLOCKED
  exit 1
}

has_cmd() {
  command -v "$1" >/dev/null 2>&1
}
