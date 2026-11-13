#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if command -v apt >/dev/null; then
  sudo apt install -y python3-gi gir1.2-gtk-4.0 gir1.2-adw-1 gir1.2-ayatanaappindicator3-0.1 libnotify-bin psmisc
elif command -v dnf >/dev/null; then
  sudo dnf install -y python3-gobject gtk4 libadwaita libayatana-appindicator-gtk3 libnotify psmisc
else
  echo "Paket yöneticisi tanınmadı; bağımlılıkları elle kur (README)." >&2
fi

install -Dm755 devpanel.py "$HOME/.local/bin/devpanel.py"
"$HOME/.local/bin/devpanel.py" install

case ":$PATH:" in
  *":$HOME/.local/bin:"*) ;;
  *) echo "Not: ~/.local/bin PATH'te değil. ~/.bashrc'ye ekle: export PATH=\"\$HOME/.local/bin:\$PATH\"" ;;
esac
