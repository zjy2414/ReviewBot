#!/bin/sh
#
# Entrypoint for the Review AI worker image.
#
# The container may be started as root (for example when a bind-mounted
# /config directory is owned by the host user). In that case we fix up
# ownership and drop privileges to the unprivileged "reviewbot" user.

set -e

if [ "$(id -u)" = "0" ]; then
    for dir in /config /repos; do
        if [ -d "$dir" ]; then
            chown reviewbot:reviewbot "$dir" 2>/dev/null || true
        fi
    done

    exec gosu reviewbot "$@"
fi

exec "$@"
