---
name: network-watch
description: Triage monitor alerts for ports, wifi, bluetooth, LAN devices and file changes
---
1. Start with `recent_alerts` (min_level WARN) and group alerts by source.
2. ports: a new EXPOSED listener is CRIT unless it belongs to a package/service the admin obviously installed
   (check with `service_status` / `listening_ports`). Unknown process on a high port = CRIT.
3. wifi: a new AP for an already-known SSID may be an evil twin or a mesh node; check `wifi_networks`. Open networks are only WARN.
4. lan / bluetooth: new devices are WARN; a new PAIRED bluetooth device is always CRIT.
5. files: a modified file in /etc/passwd, /etc/shadow, sudoers, ssh or authorized_keys is CRIT. Edits right after
   an apt upgrade are normally benign; say so if the evidence supports it.
6. Check `network_connections` for outbound connections from suspicious processes.
7. Only report and recommend. Final line must be `STATUS: OK|WARN|CRIT`.
