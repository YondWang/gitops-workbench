# Task 6 Report: Nextcloud Publisher

## Scope

- Implemented only the trusted Feature Nextcloud publisher, its focused regression coverage, and the existing Task 6 README documentation.
- Did not modify SimOS, Runner registration/configuration, root CI, Registry publisher, OTA, button jobs, Tag, branch, commit, MR, or release behavior.

## Initial Delivery

- Commit: `550a995 feat: preserve feature package variant layout`
- Consumes only the validated `feature-context.json` and `feature-publish/registry-result.json` artifacts.
- Preserves resident/deb variant paths, validates all path components before curl, verifies downloaded digests before WebDAV writes, performs idempotent MKCOL, and writes the final result atomically after all uploads.

## Review Fix Round 1

The review ruled that the Registry result is an inter-job artifact, but it is still untrusted for network routing and must never control a credential-bearing request.

- `registry_url` must equal the exact Generic Package endpoint derived from protected `CI_API_V4_URL`, protected numeric `GITOPS_FEATURE_SIMOS_PROJECT_ID`, signed `build_id`, required package name, and encoded `registry_file`. The runtime replaces the artifact value with that constructed endpoint after equality validation.
- Registry downloads no longer use `--location`. A non-`200` response, including a redirect, fails before WebDAV writes.
- Every curl request now receives only `--config <0600-temporary-file>` as a sensitive transport mechanism. The config files and their parent directory are restricted to `0600` and `0700`; they are removed by `TemporaryDirectory` on both success and failure.
- Job Token is present only in the temporary Registry-download curl configuration. The Nextcloud username/password are present only in the temporary WebDAV curl configuration. Neither is placed in command arguments, exception text, result JSON, or emitted curl stderr/stdout. Curl errors are captured and replaced with sanitized, file-oriented failures.

## Review Fix Round 2

Runner users can supply a default curlrc through `HOME` or `CURL_HOME`. A
default `location` directive would otherwise be loaded before the protected
per-request config and could re-enable redirect following for a credentialed
Registry download. Every publisher invocation is now exactly
`curl -q --config <0600-temporary-file>`: `-q` is the first curl option, which
disables default curlrc loading before the protected request configuration is
processed.

The regression test creates a default `HOME`/`CURL_HOME` curlrc containing
`location`, simulates a trusted Registry origin returning a cross-origin
redirect, and proves that only the initial download is attempted: it records
neither an external request nor the Job Token, and it performs no WebDAV write.

## Regression Coverage

`test_feature_nextcloud_publisher_preserves_variant_layout` now verifies:

- nested resident/deb layout, `201` and `405` MKCOL behavior, and no password in final JSON;
- zero curl calls for dot-dot, leading-slash, empty-segment, wrong-host, wrong-project, wrong-package, and wrong-version Registry routes;
- no redirect follow while carrying a Job Token and no WebDAV operation after redirect or corrupt download;
- no token, Nextcloud username, or password in observed curl command arguments or sanitized download/MKCOL/PUT failures;
- no remaining `.feature-package-nextcloud-*` directory and no final result on failure.
- a default curlrc `location` directive cannot override the no-redirect policy
  because every curl argv starts with `-q --config`; a cross-origin redirect
  therefore cannot receive the Job Token.

## Operational Check

No real pipeline or external upload was triggered. The protected Nextcloud endpoint must support WebDAV MKCOL with `201` on create and `405` for an existing directory. The protected Job Token must retain read access to the fixed OS/simos Generic Package endpoint.
