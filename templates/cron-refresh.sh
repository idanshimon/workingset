#!/bin/bash
# workingset brief refresh — runs nightly via cron / launchd.
#
# Install:
#   1. Copy this file to ~/.local/bin/refresh-briefs.sh
#   2. Substitute placeholders below
#   3. chmod +x ~/.local/bin/refresh-briefs.sh
#   4. Register with cron:
#        (crontab -l 2>/dev/null; echo "0 4 * * * ~/.local/bin/refresh-briefs.sh > /tmp/workingset.log 2>&1") | crontab -
#
# Placeholders to substitute:
#   <WS_PATH>     — absolute path to the ws executable
#                   (find with: which ws  OR  ls ~/.venv/bin/ws)
#   <VAULT_PATH>  — absolute path to the markdown vault
#   <BRANCH_GLOB> — glob pattern matching all branches to refresh
#                   (e.g. "cust/*" for customer-notes vaults,
#                    "*"        for a flat vault,
#                    "projects/*" for project-notes vaults)

set -euo pipefail

WS="<WS_PATH>"
VAULT="<VAULT_PATH>"
BRANCH_GLOB="<BRANCH_GLOB>"

# Verify the ws executable exists
if [ ! -x "$WS" ]; then
    echo "ERROR: ws not found at $WS" >&2
    exit 1
fi

# Verify the vault exists
if [ ! -d "$VAULT" ]; then
    echo "ERROR: vault not found at $VAULT" >&2
    exit 1
fi

cd "$VAULT"

# Incremental reindex (picks up only changed files)
"$WS" reindex

# Regenerate brief for each matching branch
for branch in $BRANCH_GLOB; do
    if [ -d "$branch" ]; then
        echo "Refreshing brief: $branch"
        "$WS" brief "$branch" --budget 8000 --write
    fi
done

echo "Done. Refresh completed at $(date)."
