#!/usr/bin/env bash
set -euo pipefail

project=/home/devman/workspace/storagespace
config="$project/deploy/systemd/mounts.conf"
dry_run=false
if [[ "${1:-}" == "--dry-run" ]]; then
    dry_run=true
elif [[ $# -ne 0 ]]; then
    echo "Usage: $0 [--dry-run]" >&2
    exit 2
fi

cd "$project"
if [[ ! -r "$config" ]]; then
    echo "Storage Space: missing mount list: $config" >&2
    exit 1
fi

# Each row maps a host mount point to its backend path and real filesystem type.
declare -a available=()
line_number=0
while read -r host_mount container_mount fs_type extra || [[ -n "${host_mount:-}" ]]; do
    ((line_number += 1))
    [[ -z "${host_mount:-}" || "$host_mount" == \#* ]] && continue
    if [[ "$host_mount" != /* || "${container_mount:-}" != /* || -z "${fs_type:-}" || -n "${extra:-}" ]]; then
        echo "Storage Space: invalid row $line_number in $config" >&2
        exit 1
    fi

    # Accessing /. triggers x-systemd.automount without creating files.
    if ! /usr/bin/timeout 10s /usr/bin/stat "${host_mount}/." >/dev/null 2>&1; then
        echo "Storage Space: $host_mount did not respond; leaving it unavailable"
        continue
    fi
    if /usr/bin/findmnt -rn -M "$host_mount" -t "$fs_type" >/dev/null; then
        available+=("$host_mount|$container_mount|$fs_type")
    else
        echo "Storage Space: $host_mount is not mounted as $fs_type"
    fi
done < "$config"

if ((${#available[@]} == 0)); then
    echo "Storage Space: no configured remote mounts are available; leaving backend unchanged"
    exit 0
fi

container_id=$(/usr/bin/docker compose ps -q backend)
mountinfo=''
if [[ -n "$container_id" ]]; then
    mountinfo=$(/usr/bin/docker exec "$container_id" cat /proc/self/mountinfo 2>/dev/null || true)
fi

for mapping in "${available[@]}"; do
    IFS='|' read -r host_mount container_mount fs_type <<< "$mapping"
    if ! /usr/bin/awk -v target="$container_mount" -v fs="$fs_type" \
        '$5 == target { for (i=1; i<=NF; i++) if ($i == "-" && $(i+1) == fs) found=1 } END { exit !found }' \
        <<< "$mountinfo"; then
        echo "Storage Space: backend misses $fs_type at $container_mount (host: $host_mount)"
        if [[ "$dry_run" == true ]]; then
            echo "Storage Space: dry run; backend would be recreated"
        else
            echo "Storage Space: recreating backend once for all configured mounts"
            /usr/bin/docker compose up -d --no-deps --force-recreate backend
        fi
        exit 0
    fi
done

echo "Storage Space: backend sees all available remote mounts"
