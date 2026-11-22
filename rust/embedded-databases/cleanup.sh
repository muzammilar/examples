#!/bin/sh
set -eu
project=rust-embedded-catalog
active=$(docker ps -q --filter "label=com.docker.compose.project=$project")
if [ -n "$active" ]; then
    echo 'Refuse cleanup while an owned container is running; preserve native state.' >&2
    exit 1
fi
already_destroyed=false
if grep -q '"state": "destroyed"' results/lab.json; then
    already_destroyed=true
else
    # Verification and byte-checked offline export precede storage deletion.
    docker compose run --rm evaluation archive
fi
docker compose down --volumes --remove-orphans
check_absence() {
    for kind in container network volume; do
        case "$kind" in
            container) remaining=$(docker ps -aq --filter "label=com.docker.compose.project=$project");;
            network) remaining=$(docker network ls -q --filter "label=com.docker.compose.project=$project");;
            volume) remaining=$(docker volume ls -q --filter "label=com.docker.compose.project=$project");;
        esac
        if [ -n "$remaining" ]; then
            echo "Owned $kind remains after cleanup: $remaining" >&2
            exit 1
        fi
    done
}
check_absence
if [ "$already_destroyed" = false ]; then
    printf '{"checked_project":"%s","containers":[],"networks":[],"volumes":[],"checked_utc":"%s"}\n' "$project" "$(date -u '+%Y-%m-%dT%H:%M:%SZ')" > results/teardown-runtime-inventory.json
    # This one-shot has no database volume; it only records the verified teardown.
    docker run --rm --label "com.docker.compose.project=$project" --network none \
        --cpus 2 --memory 2g --memory-swap 2g --user "${LOCAL_UID:-$(id -u)}:${LOCAL_GID:-$(id -g)}" \
        --mount "type=bind,source=$PWD/results,target=/results" rust-embedded-catalog:dev record-teardown
fi
images=$(docker image ls -q --filter reference=rust-embedded-catalog:dev)
if [ -n "$images" ]; then docker image rm rust-embedded-catalog:dev; fi
check_absence
remaining_image=$(docker image ls -q --filter reference=rust-embedded-catalog:dev)
if [ -n "$remaining_image" ]; then echo 'Owned image remains after cleanup' >&2; exit 1; fi
printf '%s\n' 'Owned containers, networks, volumes and image absent; local witnesses/native archives retained.'
