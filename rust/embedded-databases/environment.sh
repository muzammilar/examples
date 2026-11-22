#!/bin/sh
set -eu
{
    printf 'captured_utc='; date -u '+%Y-%m-%dT%H:%M:%SZ'
    printf 'host_os='; uname -s
    printf 'host_architecture='; uname -m
    case $(uname -s) in
        Darwin)
            printf 'host_cpu_model='; sysctl -n machdep.cpu.brand_string
            printf 'host_logical_cpus='; sysctl -n hw.ncpu
            printf 'host_memory_bytes='; sysctl -n hw.memsize
            ;;
        *) printf 'host_cpu_model='; sed -n 's/^model name[[:space:]]*: //p' /proc/cpuinfo | head -1;;
    esac
    docker info --format 'docker_server={{.ServerVersion}} docker_cpus={{.NCPU}} docker_memory_bytes={{.MemTotal}} docker_kernel={{.KernelVersion}} storage_driver={{.Driver}} cgroup_version={{.CgroupVersion}}'
    docker image inspect rust-embedded-catalog:dev --format 'workload_image_id={{.Id}} architecture={{.Architecture}} os={{.Os}}'
    printf '%s\n' 'configured_budget=2 CPUs / 2147483648 bytes memory / swap disabled / network none'
    printf '%s\n' 'co_tenancy=Other isolated functional labs may run concurrently; timings are diagnostics, not a comparative benchmark'
} > results/host-header.txt
