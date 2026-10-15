---
name: system-health
description: Baseline health check of CPU, memory, load and disk
---
1. Call `system_overview` and `top_processes`.
2. WARN if load average (1m) exceeds 2x the CPU count, MemAvailable < 10% of MemTotal, or swap use > 50%.
3. CRIT if MemAvailable < 5% or root disk > 95% used.
4. If a single process is the cause, report its name and PID. Do not kill processes; propose it.
