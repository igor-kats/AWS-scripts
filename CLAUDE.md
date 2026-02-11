# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

Multi-tool repo for AWS utility scripts. Each tool lives in its own subdirectory with its own `requirements.txt` and `README.md`. The root README is an index.

## Working Style
- Work autonomously. Do not ask for confirmation on routine decisions.
- Use subagents to parallelize independent work.
- When facing ambiguity, make the best judgment call and document it.
- Only ask the user when something is truly blocking.

## Hard Rules — NEVER do without explicit user approval:
- Do NOT deploy anything (no terraform apply, no CDK deploy, no SAM deploy, no serverless deploy)
- Do NOT run tests against production or staging data/accounts
- Do NOT modify or delete any real AWS resources
- Do NOT push to main/master branch
- All infrastructure changes — dry-run/plan only (terraform plan, cdk diff, etc.)
- All tests must use mocks, fixtures, or local data only

## Current Tools

- **ecr-cleaner/** — CLI for listing and cleaning up AWS ECR images

## Running ecr-cleaner

```bash
pip install -r ecr-cleaner/requirements.txt
python ecr-cleaner/ecr_cleaner.py --region <region> list-repos
python ecr-cleaner/ecr_cleaner.py --region <region> list-sizes
python ecr-cleaner/ecr_cleaner.py --region <region> cleanup --exclude "*latest*" --dry-run
```

## Architecture (ecr-cleaner)

`ecr_cleaner.py` is a single-file CLI tool with two layers:

- **ECRManager class** — all AWS ECR operations (list, analyze, delete) using boto3 paginators. Holds the ECR client.
- **CLI layer** — argparse with three subcommands (`list-repos`, `list-sizes`, `cleanup`), each backed by a `cmd_*` function that instantiates ECRManager and formats output.

**ECRImage** dataclass is the shared data structure passed between methods.

Key safety constraints in cleanup:
- At least one `--exclude` pattern is required (prevents accidental mass deletion)
- Empty exclusion patterns → `_should_delete` returns `False` for all images
- Untagged images are always eligible for deletion
- `--dry-run` previews without deleting; otherwise interactive confirmation is required
- Deletions are batched at 100 images (AWS API limit)

## Conventions

- No build system, no tests, no linting configured
- Each tool is self-contained in its own directory
- Python 3.10+ (uses `X | Y` union types, `list[str]` generics)
- boto3 is the only external dependency (per tool)
