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

In this GitLab version, the form is shown as **Inputs**.

Select branch:

```text
main
```

For the first test, keep the default inputs:

```text
action=list_branches
target_project=software_hmi_app/business
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
action=create_branch
branch_name=codex/api-test-from-fix
source_ref=fix
```

## Create A Policy Branch From Existing fix

If `baseline/3.2.0.0` does not exist yet, override source ref:

```text
action=create_policy_branch
branch_kind=feature
version=3.2.0.0
ticket_id=TASK1234
short_desc=user-login
source_ref=fix
```
