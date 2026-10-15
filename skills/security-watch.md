---
name: security-watch
description: Basic security signals - logins, exposed ports, brute force
---
1. Call `recent_logins`: flag many failed logins from one source (brute force) or logins from unexpected users.
2. Call `listening_ports`: flag services bound to 0.0.0.0 that are typically internal (databases, redis, ollama 11434, docker api).
3. Call `read_journal` for unit `ssh` at priority `warning`.
4. Only report and recommend (e.g. fail2ban, firewall rule). Never change firewall or SSH configuration yourself.
