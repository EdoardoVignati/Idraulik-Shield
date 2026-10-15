---
name: service-recovery
description: Detect and recover failed systemd services
---
1. Call `failed_services`. For each failed unit call `service_status` and `read_journal` with that unit.
2. Identify the root cause from logs (config error, OOM, port in use, missing dependency).
3. If the cause is transient (crash, OOM kill), request `restart_service` for that unit (one attempt only).
4. If it is a config or dependency error, do NOT restart; report the exact log line and proposed fix.
5. Never restart sshd, networking, or systemd-* units.
