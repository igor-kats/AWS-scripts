#!/usr/bin/env python3
"""CLI tool for managing and cleaning up AWS ECR images."""

import argparse
import fnmatch
import logging
import sys
from collections import defaultdict
from dataclasses import dataclass

import boto3

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
)
logger = logging.getLogger(__name__)


@dataclass
class ECRImage:
    repository: str
    digest: str
    tags: list[str]
    size_in_gb: float


class ECRManager:
    def __init__(self, profile_name: str | None, region_name: str):
        session_kwargs = {}
        if profile_name:
            session_kwargs["profile_name"] = profile_name
        session = boto3.session.Session(**session_kwargs)
        self.ecr_client = session.client("ecr", region_name=region_name)

    def get_repositories(self, name_filter: str = "") -> list[str]:
        """Return repository names, optionally filtered by substring."""
        paginator = self.ecr_client.get_paginator("describe_repositories")
        repos = []
        for page in paginator.paginate():
            for repo in page.get("repositories", []):
                name = repo["repositoryName"]
                if not name_filter or name_filter in name:
                    repos.append(name)
        return sorted(repos)

    def get_repository_sizes(self, name_filter: str = "") -> dict[str, float]:
        """Return {repo_name: total_size_gb} sorted descending by size."""
        repos = self.get_repositories(name_filter)
        if not repos:
            logger.info("No repositories found.")
            return {}

        logger.info(f"Found {len(repos)} repositories.")
        sizes: dict[str, float] = {}

        for repo_name in repos:
            logger.info(f"Analyzing: {repo_name}")
            total_bytes = 0
            paginator = self.ecr_client.get_paginator("describe_images")
            for page in paginator.paginate(repositoryName=repo_name):
                for image in page.get("imageDetails", []):
                    total_bytes += image.get("imageSizeInBytes", 0)
            sizes[repo_name] = total_bytes / (1024**3)

        return dict(sorted(sizes.items(), key=lambda x: x[1], reverse=True))

    def find_deletable_images(
        self, name_filter: str, excluded_patterns: list[str]
    ) -> list[ECRImage]:
        """Find images eligible for deletion based on tag exclusion patterns."""
        repos = self.get_repositories(name_filter)
        if not repos:
            logger.info("No matching repositories found.")
            return []

        logger.info(f"Scanning {len(repos)} repositories: {repos}")
        images: list[ECRImage] = []

        for repo_name in repos:
            logger.info(f"Analyzing: {repo_name}")
            paginator = self.ecr_client.get_paginator("describe_images")
            for page in paginator.paginate(repositoryName=repo_name):
                for detail in page.get("imageDetails", []):
                    tags = detail.get("imageTags", [])
                    if self._should_delete(tags, excluded_patterns):
                        images.append(
                            ECRImage(
                                repository=repo_name,
                                digest=detail["imageDigest"],
                                tags=tags,
                                size_in_gb=detail.get("imageSizeInBytes", 0)
                                / (1024**3),
                            )
                        )
        return images

    @staticmethod
    def _should_delete(tags: list[str], excluded_patterns: list[str]) -> bool:
        """Determine if an image should be deleted.

        An image is kept (not deleted) if any of its tags match any excluded pattern.
        Untagged images are always eligible for deletion.
        If no exclusion patterns are provided, no images are marked for deletion
        (safety: require explicit patterns to avoid accidental mass deletion).
        """
        if not excluded_patterns:
            return False
        if not tags:
            return True
        return not any(
            fnmatch.fnmatch(tag, pattern)
            for tag in tags
            for pattern in excluded_patterns
        )

    def delete_images(self, images: list[ECRImage]) -> None:
        """Delete images in batches of 100 (AWS API limit)."""
        grouped: dict[str, list[ECRImage]] = defaultdict(list)
        for img in images:
            grouped[img.repository].append(img)

        for repo_name, repo_images in grouped.items():
            logger.info(f"Deleting {len(repo_images)} images from '{repo_name}'...")
            for i in range(0, len(repo_images), 100):
                batch = repo_images[i : i + 100]
                try:
                    response = self.ecr_client.batch_delete_image(
                        repositoryName=repo_name,
                        imageIds=[{"imageDigest": img.digest} for img in batch],
                    )
                    deleted = len(response.get("imageIds", []))
                    failures = response.get("failures", [])
                    logger.info(f"  Deleted {deleted} images from '{repo_name}'.")
                    if failures:
                        logger.warning(f"  Failures: {failures}")
                except Exception as e:
                    logger.error(f"  Error deleting batch from '{repo_name}': {e}")


# -- CLI -----------------------------------------------------------------------


def cmd_list_repos(args: argparse.Namespace) -> None:
    mgr = ECRManager(args.profile, args.region)
    repos = mgr.get_repositories(args.filter)
    if not repos:
        print("No repositories found.")
        return
    for name in repos:
        print(name)


def cmd_list_sizes(args: argparse.Namespace) -> None:
    mgr = ECRManager(args.profile, args.region)
    sizes = mgr.get_repository_sizes(args.filter)
    if not sizes:
        print("No repository data available.")
        return
    print(f"\n{'Repository':<60} {'Size (GB)':>10}")
    print("-" * 72)
    for name, size_gb in sizes.items():
        print(f"{name:<60} {size_gb:>10.2f}")


def cmd_cleanup(args: argparse.Namespace) -> None:
    if not args.exclude:
        logger.error(
            "At least one --exclude pattern is required "
            "(safety: prevents accidental deletion of all images)."
        )
        sys.exit(1)

    mgr = ECRManager(args.profile, args.region)
    images = mgr.find_deletable_images(args.filter, args.exclude)

    if not images:
        print("No images matched for deletion.")
        return

    total_size = sum(img.size_in_gb for img in images)
    print(f"\nImages to delete ({len(images)} images, {total_size:.2f} GB total):\n")
    for img in images:
        tags_str = ", ".join(img.tags) if img.tags else "<untagged>"
        print(
            f"  {img.repository:<50} {tags_str:<30} {img.size_in_gb:.2f} GB"
        )
    print(f"\nTotal: {len(images)} images, {total_size:.2f} GB")

    if args.dry_run:
        print("\n[dry-run] No images were deleted.")
        return

    answer = input("\nDelete these images? (yes/no): ").strip().lower()
    if answer not in ("yes", "y"):
        print("Deletion aborted.")
        return

    mgr.delete_images(images)
    print("Done.")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ecr_cleaner",
        description="Manage and clean up AWS ECR images.",
    )
    parser.add_argument(
        "--profile", default=None, help="AWS CLI profile name (omit to use default credentials)"
    )
    parser.add_argument(
        "--region", required=True, help="AWS region (e.g. us-east-1)"
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # list-repos
    p_repos = sub.add_parser("list-repos", help="List ECR repository names")
    p_repos.add_argument(
        "--filter", default="", help="Only show repos whose name contains this substring"
    )
    p_repos.set_defaults(func=cmd_list_repos)

    # list-sizes
    p_sizes = sub.add_parser("list-sizes", help="List ECR repositories with total image sizes")
    p_sizes.add_argument(
        "--filter", default="", help="Only show repos whose name contains this substring"
    )
    p_sizes.set_defaults(func=cmd_list_sizes)

    # cleanup
    p_clean = sub.add_parser("cleanup", help="Find and delete ECR images by tag exclusion")
    p_clean.add_argument(
        "--filter", default="", help="Only target repos whose name contains this substring"
    )
    p_clean.add_argument(
        "--exclude",
        action="append",
        default=[],
        help="Tag pattern to keep (fnmatch syntax, e.g. '*latest*'). Repeatable.",
    )
    p_clean.add_argument(
        "--dry-run",
        action="store_true",
        help="Show what would be deleted without actually deleting",
    )
    p_clean.set_defaults(func=cmd_cleanup)

    return parser


def main() -> None:
    parser = build_parser()
    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()