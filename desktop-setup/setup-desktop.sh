#!/bin/bash
# Run as the desktop user in a normal Pi terminal, not with sudo.
set -euo pipefail

if [[ $EUID == 0 ]]; then
    echo "Run this script as your normal desktop user, without sudo." >&2
    exit 1
fi
if [[ $(dpkg --print-architecture) != arm64 ]]; then
    echo "This setup is prepared for the Pi's ARM64 Debian installation." >&2
    exit 1
fi

setup_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
desktop_dir=$(xdg-user-dir DESKTOP)
data_dir=${XDG_DATA_HOME:-$HOME/.local/share}
config_dir=${XDG_CONFIG_HOME:-$HOME/.config}
app_dir=$data_dir/pi-health-monitor

if ! /usr/bin/python3 -c 'import gi; gi.require_version("Gtk", "3.0"); from gi.repository import Gtk' 2>/dev/null; then
    sudo apt-get update
    sudo apt-get install -y --no-install-recommends python3-gi gir1.2-gtk-3.0
fi

install -d -- "$app_dir" "$desktop_dir" "$data_dir/applications" "$config_dir/autostart"
install -m 644 -- "$setup_dir/pi_health.py" "$app_dir/pi_health.py"
# Quote the Desktop Entry Exec argument using its own escaping rules.
/usr/bin/python3 - "$app_dir/pi_health.py" "$data_dir/applications/local.polarimeter.PiHealth.desktop" <<'PY'
import sys
from pathlib import Path
script, target = sys.argv[1:]
escaped = script.replace('\\', '\\\\').replace('"', '\\"').replace('`', '\\`').replace('$', '\\$').replace('%', '%%')
Path(target).write_text(
    '[Desktop Entry]\nVersion=1.0\nType=Application\nName=Pi Health\n'
    'Comment=Live temperature, CPU, RAM, storage and power warnings\n'
    f'Exec=/usr/bin/python3 "{escaped}"\n'
    'Icon=utilities-system-monitor\nTerminal=false\nCategories=System;Monitor;\n'
    'StartupNotify=true\nX-GNOME-Autostart-enabled=true\n', encoding='utf-8')
PY
install -m 755 -- "$data_dir/applications/local.polarimeter.PiHealth.desktop" "$desktop_dir/Pi-Health.desktop"
install -m 644 -- "$data_dir/applications/local.polarimeter.PiHealth.desktop" "$config_dir/autostart/pi-health-monitor.desktop"

if [[ -n ${WAYLAND_DISPLAY:-}${DISPLAY:-} ]]; then
    # No browser is launched during setup. Only the small monitor starts.
    nohup /usr/bin/python3 "$app_dir/pi_health.py" >/dev/null 2>&1 </dev/null &
fi
echo "Pi Health installed: Desktop shortcut + automatic start at desktop login."

install_brave() {
    # Brave's official signed APT release repository, supporting ARM64.
    # Keep repository downloads in a temporary directory until both succeed.
    sudo -v || return
    if ! command -v curl >/dev/null; then
        sudo apt-get update || return
        sudo apt-get install -y --no-install-recommends curl ca-certificates || return
    fi
    download_dir=$(mktemp -d) || return
    trap 'rm -rf -- "$download_dir"' EXIT
    curl --fail --silent --show-error --location --connect-timeout 15 --max-time 120 \
        https://brave-browser-apt-release.s3.brave.com/brave-browser-archive-keyring.gpg \
        -o "$download_dir/brave-browser-archive-keyring.gpg" || return
    curl --fail --silent --show-error --location --connect-timeout 15 --max-time 120 \
        https://brave-browser-apt-release.s3.brave.com/brave-browser.sources \
        -o "$download_dir/brave-browser-release.sources" || return
    sudo install -m 644 "$download_dir/brave-browser-archive-keyring.gpg" /usr/share/keyrings/brave-browser-archive-keyring.gpg || return
    sudo install -m 644 "$download_dir/brave-browser-release.sources" /etc/apt/sources.list.d/brave-browser-release.sources || return
    sudo apt-get update || return
    sudo apt-get install -y --no-install-recommends brave-browser || return
    install -m 755 /usr/share/applications/brave-browser.desktop "$desktop_dir/Brave.desktop" || return
    brave-browser --version || return
    echo "Brave installed with its official desktop shortcut."
}

if ! install_brave; then
    echo "Brave installation failed. Pi Health is installed. Check network/admin access, then rerun this script." >&2
    exit 1
fi
echo "Finished. Desktop icons: Brave and Pi Health."
