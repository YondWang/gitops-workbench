# GitOps Control

This project is a GitLab-native control project for managing:

```text
software_hmi_app/business
```

It uses GitLab CI manual pipelines plus GitLab REST API.

## First Run

Open this project in GitLab:

```text
Build -> Pipelines -> Run pipeline
```

Select variables:

```text
ACTION=list_branches
TARGET_PROJECT=software_hmi_app/business
```

Run the pipeline.

## Required CI/CD Variable

Configure in:

```text
Settings -> CI/CD -> Variables
```

Required:

```text
GITLAB_API_TOKEN
```

Recommended options:

```text
Masked: enabled
Protected: disabled for first test
```

After branch protection is configured, you can tighten this policy.

## Supported Actions

```text
list_branches
create_branch
create_policy_branch
protect_branch
```

## Safe First Test

```text
ACTION=list_branches
```

## Create A Test Branch

```text
ACTION=create_branch
BRANCH_NAME=codex/api-test-from-fix
SOURCE_REF=fix
```

## Create A Policy Branch From Existing fix

If `baseline/3.2.0.0` does not exist yet, override source ref:

```text
ACTION=create_policy_branch
BRANCH_KIND=feature
VERSION=3.2.0.0
TICKET_ID=TASK1234
SHORT_DESC=user-login
SOURCE_REF=fix
```

