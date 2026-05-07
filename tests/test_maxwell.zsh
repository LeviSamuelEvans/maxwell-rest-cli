#!/usr/bin/env zsh

set -u

ROOT="${0:A:h:h}"
TMP="${TMPDIR:-/tmp}/maxwell-test-$$"
MOCK_BIN="$TMP/bin"
LOG="$TMP/calls.log"
KEYCHAIN="$TMP/keychain"
CONFIG_DIR="$TMP/config"

mkdir -p "$MOCK_BIN" "$KEYCHAIN" "$CONFIG_DIR"

cleanup() {
  rm -rf "$TMP"
}
trap cleanup EXIT

cat > "$MOCK_BIN/security" <<'EOF'
#!/usr/bin/env zsh
set -u
cmd="$1"; shift
account=""
service=""
password=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    -a) account="$2"; shift 2 ;;
    -s) service="$2"; shift 2 ;;
    -w)
      if [[ "$cmd" == "find-generic-password" ]]; then
        shift
      else
        password="$2"
        shift 2
      fi
      ;;
    -U) shift ;;
    *) shift ;;
  esac
done
file="$MOCK_KEYCHAIN/$account.$service"
case "$cmd" in
  add-generic-password)
    print -r -- "$password" > "$file"
    ;;
  find-generic-password)
    [[ -f "$file" ]] || exit 44
    cat "$file"
    ;;
  delete-generic-password)
    rm -f "$file"
    ;;
  *)
    exit 2
    ;;
esac
EOF
chmod +x "$MOCK_BIN/security"

cat > "$MOCK_BIN/curl" <<'EOF'
#!/usr/bin/env zsh
set -u
print -r -- "$*" >> "$MOCK_LOG"
last="${@: -1}"
with_status=0
for arg in "$@"; do
  [[ "$arg" == "-w" ]] && with_status=1
done
emit() {
  local body="$1" code="${2:-200}"
  print -r -- "$body"
  [[ "$with_status" == "1" ]] && print -r -- "$code"
  return 0
}
case "$last" in
  *get_new_token*)
    if [[ "${MOCK_TOKEN_STATUS:-200}" != "200" ]]; then
      emit "auth failed" "${MOCK_TOKEN_STATUS}"
      exit 0
    fi
    emit "portal-token"
    ;;
  *get_new_slurm_token*)
    emit '{"token":"slurm-token"}'
    ;;
  */job/submit)
    data_file=""
    for arg in "$@"; do
      if [[ "$arg" == --data-binary ]]; then
        next_is_data=1
      elif [[ "${next_is_data:-0}" == "1" ]]; then
        data_file="${arg#@}"
        next_is_data=0
      fi
    done
    [[ -n "$data_file" ]] && cp "$data_file" "$MOCK_PAYLOAD"
    emit '{"job_id":12345}'
    ;;
  */jobs)
    emit '{"jobs":[{"job_id":12345,"job_state":"RUNNING","partition":"allcpu","name":"test","user_name":"alice","time_used":10},{"job_id":99,"job_state":"PENDING","partition":"maxcpu","name":"other","user_name":"bob"}]}'
    ;;
  */job/12345)
    emit '{"job_id":12345,"name":"test","job_state":"RUNNING","partition":"allcpu","user_name":"alice"}'
    ;;
  */slurmdb/*/job/12345)
    emit '{"job_id":12345,"name":"test","state":"COMPLETED","partition":"allcpu","user_name":"alice"}'
    ;;
  *'/job/12345?signal=SIGTERM')
    emit '{"status":"ok"}'
    ;;
  *)
    print -u2 -- "unexpected curl: $*"
    exit 7
    ;;
esac
EOF
chmod +x "$MOCK_BIN/curl"

PATH="$MOCK_BIN:$PATH"
export MOCK_KEYCHAIN="$KEYCHAIN"
export MOCK_LOG="$LOG"
export MOCK_PAYLOAD="$TMP/payload.json"
export MAXWELL_CONFIG_DIR="$CONFIG_DIR"

assert_contains() {
  local needle="$1" file="$2"
  grep -F "$needle" "$file" >/dev/null || {
    print -u2 -- "expected '$needle' in $file"
    print -u2 -- "--- $file ---"
    cat "$file" >&2
    exit 1
  }
}

zsh -n "$ROOT/bin/maxwell"
zsh -n "$ROOT/bin/maxwell-tui"
python3 -m py_compile "$ROOT/tui/maxwell_tui.py"
"$ROOT/bin/maxwell-tui" --self-test-keys >/dev/null

"$ROOT/bin/maxwell" init --user alice >/dev/null
assert_contains "MAXWELL_USER=alice" "$CONFIG_DIR/config.env"
assert_contains "MAXWELL_DEFAULT_PARTITION=allcpu" "$CONFIG_DIR/config.env"

if print -r -- "bad-pw" | env MOCK_TOKEN_STATUS=401 "$ROOT/bin/maxwell" auth login >"$TMP/login.out" 2>"$TMP/login.err"; then
  print -u2 -- "expected auth login to fail"
  exit 1
fi
assert_contains "portal token request failed (HTTP 401" "$TMP/login.err"

print -r -- "pw" | "$ROOT/bin/maxwell" auth login >/dev/null
"$ROOT/bin/maxwell" auth refresh >/dev/null
[[ "$(cat "$KEYCHAIN/alice.maxwell-rest.portal-token")" == "portal-token" ]] || exit 1
[[ "$(cat "$KEYCHAIN/alice.maxwell-rest.slurm-token")" == "slurm-token" ]] || exit 1

script="$TMP/job.sh"
{
  print -r -- '#!/usr/bin/env bash'
  print -r -- 'echo "quoted"'
  print -r -- 'sleep 1'
} > "$script"

dry_payload="$("$ROOT/bin/maxwell" submit "$script" --name dry-job --time 100 --dry-run)"
print -r -- "$dry_payload" | jq -e '.job.name == "dry-job" and .job.cpus_per_task == 1 and .job.tasks == 1 and .job.memory_per_node.number == 1000 and (.script | contains("sleep 1"))' >/dev/null
[[ ! -f "$MOCK_PAYLOAD" ]] || rm -f "$MOCK_PAYLOAD"

submit_output="$("$ROOT/bin/maxwell" submit "$script" --name quoted-job --time 100 --json)"
[[ "$submit_output" == '{"job_id":12345}' ]] || {
  print -u2 -- "unexpected submit output: $submit_output"
  exit 1
}
jq -e '
  .job.partition == "allcpu" and
  .job.name == "quoted-job" and
  .job.time_limit.number == 100 and
  .job.cpus_per_task == 1 and
  .job.tasks == 1 and
  .job.memory_per_node.number == 1000 and
  (.script | contains("echo \"quoted\""))
' "$MOCK_PAYLOAD" >/dev/null

jobs_json="$("$ROOT/bin/maxwell" jobs --json)"
print -r -- "$jobs_json" | jq -e '.jobs | length == 1 and .[0].user_name == "alice"' >/dev/null

doctor_json="$("$ROOT/bin/maxwell" doctor --json)"
print -r -- "$doctor_json" | jq -e '.checks[] | select(.name == "slurm-api" and .status == "ok")' >/dev/null

tui_check="$(MAXWELL_BIN="$ROOT/bin/maxwell" "$ROOT/bin/maxwell-tui" --check)"
[[ "$tui_check" == "jobs=1 user=alice" ]] || {
  print -u2 -- "unexpected TUI check output: $tui_check"
  exit 1
}

job_json="$("$ROOT/bin/maxwell" job 12345 --json)"
print -r -- "$job_json" | jq -e '.job_id == 12345' >/dev/null

history_json="$("$ROOT/bin/maxwell" history 12345 --json)"
print -r -- "$history_json" | jq -e '.state == "COMPLETED"' >/dev/null

cancel_json="$("$ROOT/bin/maxwell" cancel 12345 --signal SIGTERM --json)"
[[ "$cancel_json" == '{"status":"ok"}' ]] || exit 1
assert_contains "/sapi/slurm/v0.0.44/job/12345?signal=SIGTERM" "$LOG"

"$ROOT/bin/maxwell" auth logout >/dev/null
[[ ! -f "$KEYCHAIN/alice.maxwell-rest.portal-token" ]] || exit 1
[[ ! -f "$KEYCHAIN/alice.maxwell-rest.slurm-token" ]] || exit 1

print -- "ok"
