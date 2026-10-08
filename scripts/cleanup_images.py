"""Remove GHCR image versions whose release tags no longer exist."""

import argparse
import json
import os
import re
import subprocess

REPOSITORY = "planetic-labs/storagespace"
RELEASE_TAG = re.compile(r"v\d{4}\.\d{2}\.\d{2}(?:-patch\d+)?\Z")


def gh_json(*args):
    result = subprocess.run(["gh", *args], check=True, capture_output=True, text=True)
    return json.loads(result.stdout)


def retained_release_tags():
    releases = gh_json(
        "release", "list", "--repo", REPOSITORY, "--limit", "1000",
        "--json", "tagName,isDraft,isPrerelease",
    )
    return {
        release["tagName"] for release in releases
        if not release["isDraft"] and not release["isPrerelease"]
        and RELEASE_TAG.fullmatch(release["tagName"])
    }


def package_versions(component):
    versions = []
    page = 1
    while True:
        batch = gh_json(
            "api", f"orgs/planetic-labs/packages/container/storagespace-{component}/versions"
            f"?per_page=100&page={page}"
        )
        versions.extend(batch)
        if len(batch) < 100:
            return versions
        page += 1


def versions_to_delete(versions, retained_tags):
    for version in versions:
        tags = version.get("metadata", {}).get("container", {}).get("tags", [])
        release_tags = [tag for tag in tags if RELEASE_TAG.fullmatch(tag)]
        if release_tags and not any(tag in retained_tags for tag in release_tags):
            yield version["id"], release_tags


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--component", required=True, choices=("backend", "frontend"))
    parser.add_argument("--apply", action="store_true", help="Delete instead of printing the plan")
    args = parser.parse_args()

    if os.environ.get("GITHUB_REPOSITORY") != REPOSITORY:
        parser.error(f"GITHUB_REPOSITORY must be {REPOSITORY}")

    retained = retained_release_tags()
    candidates = list(versions_to_delete(package_versions(args.component), retained))
    for version_id, tags in candidates:
        print(f"{'Deleting' if args.apply else 'Would delete'} {args.component} version {version_id}: {', '.join(tags)}")
        if args.apply:
            subprocess.run(
                ["gh", "api", "--method", "DELETE",
                 f"orgs/planetic-labs/packages/container/storagespace-{args.component}/versions/{version_id}"],
                check=True,
            )
    print(f"{len(candidates)} old tagged {args.component} image versions")


if __name__ == "__main__":
    main()
