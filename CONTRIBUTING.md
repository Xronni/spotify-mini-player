# Contributing to Spotify Mini Player

Thank you for your interest in contributing to **Spotify Mini Player**! 🎵

We welcome contributions of all kinds: bug reports, feature suggestions, documentation enhancements, UI/UX polish, translations, and code improvements.

---

## 🛠️ Local Development Setup

### System Prerequisites
- **Linux** (Ubuntu 22.04+, Debian 12+, Arch Linux, Fedora)
- **Python 3.10 or higher**
- **Spotify Desktop Client** for Linux

Install runtime and development packages:

```bash
# Ubuntu / Debian
sudo apt update && sudo apt install python3 python3-gi python3-gi-cairo gir1.2-gtk-4.0 gir1.2-adw-1 python3-cairo libx11-6 git

# Arch Linux
sudo pacman -S python python-gobject gtk4 libadwaita python-cairo libx11 git

# Fedora
sudo dnf install python3 python3-gobject gtk4 libadwaita python3-cairo libX11 git
```

### Running Locally
```bash
git clone https://github.com/Xronni/spotify-mini-player.git
cd spotify-mini-player
./run.sh
```

---

## 🧪 Automated Testing & Verification

Before opening a Pull Request, verify that your changes pass all automated checks:

```bash
./test.sh
```

The verification suite checks system dependencies, assets, bytecode compilation, dictionary parity across 5 languages, and core component instantiation.

---

## 🌍 Adding or Updating Translations (i18n)

Spotify Mini Player supports 5 languages (`EN`, `RU`, `DE`, `ES`, `FR`) in [`i18n.py`](i18n.py):
1. When introducing a new UI string, add it to all language dictionaries.
2. Run `./test.sh` to automatically verify translation parity across all languages.

---

## 📦 Pull Request Guidelines

1. Fork the repository and create a feature branch:
   ```bash
   git checkout -b feat/my-improvement
   ```
2. Make your changes and test them locally with `./run.sh` and `./test.sh`.
3. Follow Conventional Commits format (`feat: ...`, `fix: ...`, `docs: ...`).
4. Push your branch and submit a Pull Request on GitHub.
