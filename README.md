# Flatpak Manager

A GTK3 frontend for Flatpak — search, install, update, run and remove Flatpak apps and their extensions, with live installation output.

![Status: Linux-only](https://img.shields.io/badge/platform-Linux-blue)
![License: MIT](https://img.shields.io/badge/license-MIT-green)

## Features

- **Search** — find apps on Flathub by name or keyword
- **Installed apps** — list everything currently installed, with status indicators
- **Install / uninstall / update** — single apps or all at once, with live output and a progress bar
- **App info** — view detailed metadata for any app
- **Run apps** — launch directly, optionally inside a terminal (auto-detects gnome-terminal, konsole, xfce4-terminal, mate-terminal, terminator, xterm)
- **Extensions / plugins** — find and bulk-install extensions for a selected app (e.g. OBS plugins)
- **Update check** — see which apps have updates available before applying them
- **Multi-language** — English and German, switchable in the app with system language auto-detection

## Requirements

System packages (Debian/Ubuntu/Mint):

```
sudo apt install python3-gi gir1.2-gtk-3.0 flatpak
```

Fedora:
```
sudo dnf install python3-gobject gtk3 flatpak
```

Arch:
```
sudo pacman -S python-gobject gtk3 flatpak
```

Flathub should be configured as a remote (`flatpak remote-add --if-not-exists flathub https://flathub.org/repo/flathub.flatpakrepo`).

## Installation

```
git clone https://github.com/NoCoderGHG/flatpak-manager.git
cd flatpak-manager
python3 flatpak_manager.py
```

No pip dependencies. No virtual environment needed.

## Configuration

Language preference is stored in `~/.config/flatpak-manager/config.json`.

## License

MIT — see [LICENSE](LICENSE).
