#!/bin/bash
# Linux (GNOME): agentni kompyuter yoqilganda o'zi ishga tushadigan qiladi (sudo shart emas).
DIR="$(cd "$(dirname "$0")" && pwd)"
[ -f "$DIR/agent.conf" ] || { cp "$DIR/agent.conf.example" "$DIR/agent.conf"; echo "agent.conf yaratildi — ichiga tokenni yozing va qayta ishga tushiring."; exit 1; }
mkdir -p ~/.config/autostart
cat > ~/.config/autostart/terryjar-print-agent.desktop <<DESK
[Desktop Entry]
Type=Application
Name=Terry Jar pechat agenti
Exec=bash -c 'python3 "$DIR/terryjar_print_agent.py" >> "$DIR/agent.log" 2>&1'
X-GNOME-Autostart-enabled=true
DESK
nohup python3 "$DIR/terryjar_print_agent.py" >> "$DIR/agent.log" 2>&1 &
echo "Tayyor. Agent ishlayapti, jurnal: $DIR/agent.log"
