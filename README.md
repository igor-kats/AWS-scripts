# aws-scripts

Collection of CLI tools that fill gaps in the AWS Console and native AWS tooling.

## Tools

### [ecr-cleaner](ecr-cleaner/)

AWS ECR lifecycle policies support filtering images by tag prefixes (e.g. "starts with `dev`"), but they **cannot** express "delete all images whose tags do **not** contain a given pattern". This makes it impossible to write a policy like "keep only `*latest*` and `*release*`, delete everything else" using the AWS Console alone.

`ecr-cleaner` solves this by letting you specify exclusion patterns with fnmatch/glob syntax (`--exclude "*latest*" --exclude "*release*"`). It lists matching images, shows a summary, and prompts for confirmation before deleting. Supports `--dry-run` for safe previewing.

### [idle-gateways-detector](idle-gateways-detector/)

The AWS Console shows NAT Gateway and Internet Gateway existence but provides no straightforward way to see which ones are actually carrying traffic vs. sitting idle and costing money. CloudWatch metrics exist but require manual dashboard setup per gateway.

`idle-gateways-detector` automatically scans all NAT and Internet Gateways in a region, pulls 90 days of CloudWatch traffic metrics, and computes an idle percentage for each gateway. Outputs a terminal summary and a detailed Excel report — useful for cost optimization reviews.

## License

MIT
