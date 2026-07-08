log() { printf '[INFO] %s\n' "$*"; }
ok() { printf '[OK] %s\n' "$*"; }
warn() { printf '[WARN] %s\n' "$*" >&2; }
err() { printf '[ERROR] %s\n' "$*" >&2; }

check_token() {
  if [ -z "${GITLAB_API_TOKEN:-}" ]; then
    err "GITLAB_API_TOKEN is missing. Configure it in Settings -> CI/CD -> Variables."
    exit 2
  fi
}

urlencode_project() {
  printf '%s' "$1" | sed 's#/#%2F#g'
}

urlencode_ref() {
  printf '%s' "$1" | sed 's#/#%2F#g; s# #%20#g'
}

init_project() {
  PROJECT_ID="$(urlencode_project "$TARGET_PROJECT")"
  PROJECT_API="/projects/${PROJECT_ID}"
  export PROJECT_ID PROJECT_API
  log "target project: ${TARGET_PROJECT}"
  log "operation: ${OPERATION:-manual button}"
}

api() {
  method="$1"
  path="$2"
  shift 2
  curl --fail-with-body --silent --show-error \
    --request "$method" \
    --header "PRIVATE-TOKEN: ${GITLAB_API_TOKEN}" \
    "$@" \
    "${GITLAB_API_URL}${path}"
}

api_get() {
  api GET "$1"
}

check_version() {
  printf '%s' "$1" | grep -Eq '^[0-9]+\.[0-9]+\.[0-9]+\.[0-9]+$'
}

require_version() {
  check_version "$1" || {
    err "version must be A.B.C.D, got: $1"
    exit 2
  }
}

increment_patch() {
  require_version "$1"
  printf '%s' "$1" | awk -F. '{print $1"."$2"."$3"."$4+1}'
}

get_major_minor() {
  printf '%s' "$1" | awk -F. '{print $1"."$2}'
}

clean_name() {
  value="${1:-work}"
  printf '%s' "$value" \
    | tr '[:upper:]' '[:lower:]' \
    | sed -E 's/[^a-z0-9._-]+/-/g; s/-+/-/g; s/^[-._]+//; s/[-._]+$//' \
    | sed 's/^$/work/'
}

upper_ticket() {
  value="${1:-TASK}"
  printf '%s' "$value" \
    | tr '[:lower:]' '[:upper:]' \
    | sed -E 's/[^A-Z0-9]+//g; s/^$/TASK/'
}

tag_source_name() {
  require_safe_ref_name "$1" "tag source"
  printf '%s' "$1" | sed 's#/#-#g'
}

tag_date_now() {
  date '+%Y%m%d%H%M'
}

version_from_ref_name() {
  printf '%s' "$1" | grep -oE '[Vv]?[0-9]+(\.[0-9]+)+' | head -n1
}

compose_tag_name() {
  source_ref="$1"
  version_value="${2:-$(version_from_ref_name "$source_ref")}"
  if [ -z "$version_value" ]; then
    err "cannot derive version from ref for tag naming: ${source_ref}"
    exit 2
  fi
  printf '%s_%s_%s' "$(tag_source_name "$source_ref")" "$version_value" "$(tag_date_now)"
}

require_safe_ref_name() {
  value="$1"
  label="${2:-ref}"
  if [ -z "$value" ]; then
    err "${label} cannot be empty"
    exit 2
  fi
  case "$value" in
    *%2F*|*%2f*|*%25*|*%*)
      err "${label} looks URL-encoded, refuse to continue: ${value}"
      exit 2
      ;;
    *' '*)
      err "${label} cannot contain spaces: ${value}"
      exit 2
      ;;
    */.|*.|*/..|*..*|*@{*|*\\*)
      err "${label} contains unsafe git ref characters: ${value}"
      exit 2
      ;;
  esac
}

branch_exists() {
  encoded_branch_ref="$(urlencode_ref "$1")"
  api GET "${PROJECT_API}/repository/branches/${encoded_branch_ref}" >/dev/null 2>&1
}

branch_create() {
  branch="$1"
  ref="$2"
  require_safe_ref_name "$branch" "branch"
  require_safe_ref_name "$ref" "source ref"
  if branch_exists "$branch"; then
    warn "branch already exists: ${branch}"
    return 0
  fi
  log "create branch: ${branch} from ${ref}"
  api POST "${PROJECT_API}/repository/branches" \
    --data-urlencode "branch=${branch}" \
    --data-urlencode "ref=${ref}"
  printf '\n'
  ok "branch created: ${branch}"
}

branch_protect() {
  name="$1"
  push_level="$2"
  merge_level="$3"
  require_safe_ref_name "$name" "protected branch"
  log "protect branch: ${name} push=${push_level} merge=${merge_level}"
  if api POST "${PROJECT_API}/protected_branches" \
    --data-urlencode "name=${name}" \
    --data "push_access_level=${push_level}" \
    --data "merge_access_level=${merge_level}"; then
    printf '\n'
    ok "protected: ${name}"
  else
    printf '\n'
    warn "protect branch failed or already exists: ${name}"
  fi
}

get_baseline_from_feature() {
  printf '%s' "$1" | awk -F/ '{print "baseline/"$2}'
}

get_baseline_from_bugfix() {
  printf '%s' "$1" | awk -F/ '{print "baseline/"$2}'
}

get_version_from_bugfix() {
  printf '%s' "$1" | awk -F/ '{print $2}'
}

get_version_from_feature() {
  printf '%s' "$1" | awk -F/ '{print $2}'
}

get_fix_from_bugfix() {
  bugfix_version_value="$(get_version_from_bugfix "$1")"
  latest_fix_branch "$bugfix_version_value"
}

latest_fix_branch() {
  latest_fix_version="$1"
  latest_fix_search="fix/${latest_fix_version}-rc"
  api GET "${PROJECT_API}/repository/branches?per_page=100&search=${latest_fix_search}" \
    | grep -o "fix/${latest_fix_version}-rc[0-9][0-9]*" \
    | sort -t c -k2,2n \
    | tail -1
}

next_rc_number() {
  next_rc_version="$1"
  next_rc_search="fix/${next_rc_version}-rc"
  next_rc_current="$(api GET "${PROJECT_API}/repository/branches?per_page=100&search=${next_rc_search}" \
    | grep -o "fix/${next_rc_version}-rc[0-9][0-9]*" \
    | sed -E "s#fix/${next_rc_version}-rc##" \
    | sort -n \
    | tail -1 || true)"
  if [ -z "$next_rc_current" ]; then
    printf '1'
  else
    awk "BEGIN { print ${next_rc_current} + 1 }"
  fi
}

create_mr() {
  source="$1"
  target="$2"
  title="$3"
  require_safe_ref_name "$source" "MR source branch"
  require_safe_ref_name "$target" "MR target branch"
  log "create MR: ${source} -> ${target}"
  api POST "${PROJECT_API}/merge_requests" \
    --data-urlencode "source_branch=${source}" \
    --data-urlencode "target_branch=${target}" \
    --data-urlencode "title=${title}" \
    --data "remove_source_branch=false"
  printf '\n'
  ok "MR requested: ${source} -> ${target}"
}

create_tag() {
  tag="$1"
  ref="$2"
  message="${3:-$tag}"
  require_safe_ref_name "$tag" "tag"
  require_safe_ref_name "$ref" "tag ref"
  log "create tag: ${tag} on ${ref}"
  api POST "${PROJECT_API}/repository/tags" \
    --data-urlencode "tag_name=${tag}" \
    --data-urlencode "ref=${ref}" \
    --data-urlencode "message=${message}"
  printf '\n'
  ok "tag created: ${tag}"
}

compare_branches() {
  require_safe_ref_name "$1" "compare from"
  require_safe_ref_name "$2" "compare to"
  compare_from_encoded="$(urlencode_ref "$1")"
  compare_to_encoded="$(urlencode_ref "$2")"
  api GET "${PROJECT_API}/repository/compare?from=${compare_from_encoded}&to=${compare_to_encoded}"
}

list_compare_commit_ids() {
  compare_source_ref="$1"
  compare_target_ref="$2"
  compare_branches "$compare_source_ref" "$compare_target_ref" \
    | grep -o '"id":"[0-9a-f][0-9a-f]*"' \
    | sed -E 's/"id":"([^"]+)"/\1/'
}

cherry_pick_commits() {
  cherry_source_branch="$1"
  cherry_target_branch="$2"
  require_safe_ref_name "$cherry_source_branch" "cherry-pick source branch"
  require_safe_ref_name "$cherry_target_branch" "cherry-pick target branch"
  cherry_commits="$(list_compare_commit_ids "$cherry_target_branch" "$cherry_source_branch" || true)"
  if [ -z "$cherry_commits" ]; then
    ok "no commits to cherry-pick: ${cherry_source_branch} -> ${cherry_target_branch}"
    return 0
  fi
  for cherry_sha in $cherry_commits; do
    log "cherry-pick ${cherry_sha} -> ${cherry_target_branch}"
    if ! api POST "${PROJECT_API}/repository/commits/${cherry_sha}/cherry_pick" \
      --data-urlencode "branch=${cherry_target_branch}"; then
      printf '\n'
      err "cherry-pick failed: ${cherry_sha} -> ${cherry_target_branch}. Resolve conflicts manually."
      exit 3
    fi
    printf '\n'
  done
  ok "cherry-pick completed: ${cherry_source_branch} -> ${cherry_target_branch}"
}

derive_tag_from_fix() {
  compose_tag_name "$1"
}

derive_tag_from_hotfix() {
  compose_tag_name "$1"
}

derive_feature_branch() {
  require_version "$VERSION"
  ticket="$(upper_ticket "$TASK_ID")"
  desc="$(clean_name "$DESCRIPTION")"
  printf 'feature/%s/%s-%s' "$VERSION" "$ticket" "$desc"
}

derive_bugfix_branch() {
  require_version "$VERSION"
  bug="$(upper_ticket "$BUG_ID")"
  desc="$(clean_name "$DESCRIPTION")"
  printf 'bugfix/%s/%s-%s' "$VERSION" "$bug" "$desc"
}

derive_hotfix_branch() {
  new_version="$(increment_patch "$CURRENT_VERSION")"
  bug="$(upper_ticket "$BUG_ID")"
  desc="$(clean_name "$DESCRIPTION")"
  printf 'hotfix/%s/%s-%s' "$new_version" "$bug" "$desc"
}

resolve_fix_branch() {
  if [ -n "${FIX_BRANCH:-}" ]; then
    printf '%s' "$FIX_BRANCH"
    return 0
  fi
  require_version "$VERSION"
  if [ -n "${RC_NUMBER:-}" ]; then
    printf 'fix/%s-rc%s' "$VERSION" "$RC_NUMBER"
    return 0
  fi
  resolved_latest_fix="$(latest_fix_branch "$VERSION" || true)"
  if [ -z "$resolved_latest_fix" ]; then
    err "cannot find fix branch for version ${VERSION}. Create fix branch first or set rc_number/fix_branch."
    exit 2
  fi
  printf '%s' "$resolved_latest_fix"
}
