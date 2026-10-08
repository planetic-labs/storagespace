#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(dirname "$SCRIPT_DIR")"
REPOSITORY="planetic-labs/storagespace"
WORKFLOW="docker-publish.yml"
DRY_RUN=false

if [[ "${1:-}" == "--dry-run" && $# -eq 1 ]]; then
    DRY_RUN=true
elif (( $# != 0 )); then
    echo "Usage: $0 [--dry-run]" >&2
    exit 2
fi

cd "$PROJECT_ROOT"

for command in git gh; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Required command is not installed: $command" >&2
        exit 1
    fi
done

if [[ "$DRY_RUN" == false ]]; then
    if [[ -n "$(git status --porcelain)" ]]; then
        echo "The working tree must be clean before creating a release." >&2
        exit 1
    fi
    if [[ "$(git branch --show-current)" != main ]]; then
        echo "Switch to main before creating a release." >&2
        exit 1
    fi
    gh auth status >/dev/null
    git fetch origin main --tags
    git pull --ff-only origin main
fi

DATE_TAG="$(date -u +'%Y.%m.%d')"
LAST_TAG_TODAY="$(git tag -l "v${DATE_TAG}" "v${DATE_TAG}-patch*" | sort -V | tail -n 1)"
if [[ -z "$LAST_TAG_TODAY" ]]; then
    VERSION="v${DATE_TAG}"
elif [[ "$LAST_TAG_TODAY" =~ ^v${DATE_TAG}-patch([0-9]+)$ ]]; then
    VERSION="v${DATE_TAG}-patch$((BASH_REMATCH[1] + 1))"
else
    VERSION="v${DATE_TAG}-patch1"
fi

if [[ "$DRY_RUN" == true ]]; then
    echo "Next release: $VERSION"
    echo "Images: ghcr.io/${REPOSITORY}-backend:${VERSION} and ghcr.io/${REPOSITORY}-frontend:${VERSION}"
    exit 0
fi

HEAD_SHA="$(git rev-parse HEAD)"
REMOTE_SHA="$(gh api "repos/${REPOSITORY}/git/ref/heads/main" --jq .object.sha)"
if [[ "$HEAD_SHA" != "$REMOTE_SHA" ]]; then
    echo "main advanced on GitHub; pull the latest commits and retry." >&2
    exit 1
fi

RELEASE_URL="$(gh release create "$VERSION" \
    --repo "$REPOSITORY" \
    --target "$HEAD_SHA" \
    --title "$VERSION" \
    --generate-notes)"
echo "Created release: $RELEASE_URL"

RUN_ID=""
for _ in {1..12}; do
    RUN_ID="$(gh run list \
        --repo "$REPOSITORY" \
        --workflow "$WORKFLOW" \
        --event release \
        --limit 10 \
        --json databaseId,displayTitle \
        --jq ".[] | select(.displayTitle == \"$VERSION\") | .databaseId" | head -n 1)"
    if [[ -n "$RUN_ID" ]]; then
        break
    fi
    sleep 5
done

if [[ -z "$RUN_ID" ]]; then
    echo "Release created, but the Docker publication run was not found." >&2
    exit 1
fi

gh run watch "$RUN_ID" --repo "$REPOSITORY" --exit-status
echo "Published ghcr.io/${REPOSITORY}-backend:${VERSION}"
echo "Published ghcr.io/${REPOSITORY}-frontend:${VERSION}"
