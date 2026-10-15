---
name: disk-pressure
description: Handle low disk space or inode exhaustion
---
1. Use `disk_usage` on `/`, `/var`, `/home`. WARN at 85% used (space or inodes), CRIT at 95%.
2. To find culprits use `run_command` with `du -xh --max-depth=2 /var` (read-only commands only).
3. Safe cleanup candidates to propose: `apt-get clean`, `journalctl --vacuum-size=200M`. Propose, never delete user data.
