#!/usr/bin/env bash
# Idraulik Shield installer for Debian/Ubuntu. Run as root from the repo: sudo ./install.sh [--with-ollama]
set -euo pipefail

[ "$(id -u)" -eq 0 ] || { echo "Run as root"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"

apt-get update -qq
apt-get install -y -qq python3 python3-venv curl iproute2 >/dev/null
# Optional hardware monitors (wifi via NetworkManager, bluetooth via bluez); skipped silently if unavailable
apt-get install -y -qq network-manager bluez >/dev/null 2>&1 || true

if [ "${1:-}" = "--with-ollama" ] && ! command -v ollama >/dev/null; then
  curl -fsSL https://ollama.com/install.sh | sh
  ollama pull qwen2.5:7b || echo "warning: model pull failed, run 'ollama pull <model>' later"
fi

python3 -m venv /opt/idraulik/venv
/opt/idraulik/venv/bin/pip install -q --upgrade pip "$SRC"

install -d -m 755 /etc/idraulik/skills /var/log/idraulik
[ -f /etc/idraulik/idraulik.toml ] || install -m 644 "$SRC/config/idraulik.toml" /etc/idraulik/idraulik.toml
[ -f /etc/idraulik/secrets.env ] || install -m 600 "$SRC/config/secrets.env.example" /etc/idraulik/secrets.env
for f in "$SRC"/skills/*.md; do
  [ -f "/etc/idraulik/skills/$(basename "$f")" ] || install -m 644 "$f" /etc/idraulik/skills/
done
ln -sf /opt/idraulik/venv/bin/idraulik /usr/local/bin/idraulik

install -m 644 "$SRC/systemd/idraulik.service" /etc/systemd/system/idraulik.service
systemctl daemon-reload
systemctl enable --now idraulik
echo "Idraulik Shield installed. Edit /etc/idraulik/idraulik.toml, then: systemctl restart idraulik"
echo "Try: idraulik monitor | idraulik alerts | idraulik check | tail -f /var/log/idraulik/alerts.log"
