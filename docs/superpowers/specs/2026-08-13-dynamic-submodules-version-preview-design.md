# Dynamic Submodules And Version Preview

## Goal

Make release packages follow the repositories enabled in repository management, keep every enabled code repository's SimOS gitlink synchronized, calculate versions from the selected SimOS branch's `software.yaml`, and preview the resulting version immediately after branch selection.

## Design

`RepositoryConfig` gains `submodule_path`. SimOS remains the main repository and config remains excluded from code releases. Every other enabled repository must provide a unique `src/...` path. Release resolution derives the component name from the repository configuration and records the selected source commit for every enabled repository.

The version update plan reads `software.yaml` at the selected SimOS ref and parses its four-part `version`. The existing component revision bitmask remains the increment rule. A weekly bump increments the third component after the normal fourth-component calculation and preserves that calculated fourth component. Invalid or missing remote version data fails precheck.

The manual admin release form sends the source ref, fallback ref, version prefix, and weekly-bump flag to a preview endpoint. The endpoint uses the same resolver as the actual release and returns current version, next version, changed components, tag name, and component commit resolutions. The form renders this response whenever the source or version controls change.

## Safety And Errors

- Missing, duplicate, or non-`src/...` submodule paths fail before any write.
- A missing or malformed remote `software.yaml` version fails before any MR or Tag.
- All repositories are prechecked before the version MR or component Tags are created.
- The git-based version update writes all configured gitlinks with mode `160000` in one commit.

## Verification

Add unit tests for repository path persistence, dynamic gitlink mapping, software version parsing, weekly bump behavior, preview responses, and manual release payload propagation. Run the full Python unittest suite and `git diff --check`.
