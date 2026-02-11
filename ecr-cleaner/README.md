# ecr-repo-cleaner

CLI tool for managing and cleaning up images in AWS Elastic Container Registry (ECR).

Deletes images based on repository name filters and tag exclusion patterns — useful for cleanup logic that AWS lifecycle policies can't express (e.g. "delete everything **except** tags matching `*latest*` or `*release*`").

## Prerequisites

- Python 3.10+
- AWS CLI configured with a profile (or default credentials / environment variables)
- IAM permissions: `ecr:DescribeRepositories`, `ecr:DescribeImages`, `ecr:BatchDeleteImage`

## Install

```bash
pip install -r requirements.txt
```

## Usage

All commands require `--region`. Use `--profile` to select a named AWS CLI profile (omit to use default credentials).

### List repositories

```bash
python ecr_cleaner.py --region us-east-1 list-repos
python ecr_cleaner.py --region us-east-1 list-repos --filter myapp
```

### List repositories with image sizes

```bash
python ecr_cleaner.py --region us-east-1 list-sizes
python ecr_cleaner.py --region us-east-1 list-sizes --filter myapp
```

### Clean up images

Find and delete images whose tags don't match any `--exclude` pattern. At least one `--exclude` is required to prevent accidental mass deletion.

```bash
# Dry run — show what would be deleted
python ecr_cleaner.py --region us-east-1 cleanup \
  --filter myapp \
  --exclude "*latest*" \
  --exclude "*release*" \
  --dry-run

# Actually delete
python ecr_cleaner.py --region us-east-1 cleanup \
  --filter myapp \
  --exclude "*latest*" \
  --exclude "*release*"
```

The cleanup command will:
1. Find repositories matching `--filter` (all repos if omitted)
2. Identify images whose tags don't match any `--exclude` pattern
3. Show a summary and prompt for confirmation before deleting
4. Delete in batches of 100 (AWS API limit)

Untagged images are always eligible for deletion. Images with at least one tag matching an exclusion pattern are kept.

## Using with environment variables

Instead of `--profile`, you can export credentials directly:

```bash
export AWS_ACCESS_KEY_ID=...
export AWS_SECRET_ACCESS_KEY=...
python ecr_cleaner.py --region us-east-1 list-repos
```

## Limitations

- AWS limits batch deletion to 100 images per API call (handled automatically).
- Exclusion patterns match against image tags only; untagged images are always eligible for deletion.

## License

MIT