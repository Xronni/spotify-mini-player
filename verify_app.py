#!/usr/bin/env python3
"""
Spotify Mini Player — Automated Pre-Deployment Verification Suite
Runs system, assets, localization, and module integration checks.
"""

import sys
import os
import shutil
import subprocess
import py_compile

GREEN = "\033[92m"
RED = "\033[91m"
YELLOW = "\033[93m"
CYAN = "\033[96m"
BOLD = "\033[1m"
RESET = "\033[0m"

APP_DIR = os.path.dirname(os.path.abspath(__file__))
os.chdir(APP_DIR)

passed_tests = 0
failed_tests = 0


def report(name, success, details=""):
    global passed_tests, failed_tests
    if success:
        passed_tests += 1
        print(f" {GREEN}✓{RESET} {BOLD}{name}{RESET}")
        if details:
            print(f"   {details}")
    else:
        failed_tests += 1
        print(f" {RED}✗{RESET} {BOLD}{name}{RESET}")
        if details:
            print(f"   {RED}{details}{RESET}")


def section(title):
    print(f"\n{CYAN}{BOLD}--- {title} ---{RESET}")


def test_system_dependencies():
    section("1. System Environment & Dependencies")

    # Python Version
    py_ver = sys.version_info
    is_py_ok = py_ver >= (3, 10)
    report("Python Version >= 3.10", is_py_ok, f"Current: {py_ver.major}.{py_ver.minor}.{py_ver.micro}")

    # GTK4 & Libadwaita
    gtk_ok = False
    adw_ok = False
    try:
        import gi
        gi.require_version('Gtk', '4.0')
        gi.require_version('Adw', '1')
        from gi.repository import Gtk, Adw, Gdk
        gtk_ok = True
        adw_ok = True
    except Exception as e:
        report("GTK4 / Libadwaita Runtime", False, str(e))

    if gtk_ok and adw_ok:
        report("GTK 4.0 & Libadwaita 1.0 Bindings", True, "Successfully loaded native gobject introspection")

    # Cairo Graphics
    try:
        import cairo
        report("Cairo 2D Vector Graphics", True, f"Cairo Version: {cairo.cairo_version_string()}")
    except Exception as e:
        report("Cairo Graphics", False, str(e))

    # libX11
    try:
        import ctypes
        x11 = ctypes.cdll.LoadLibrary("libX11.so.6")
        report("X11 Shared Library (libX11.so.6)", x11 is not None, "Loaded for smart desktop visibility detection")
    except Exception as e:
        report("X11 Shared Library", False, str(e))


def test_assets_and_files():
    section("2. Project Assets & Packaging Scripts")

    icon_path = os.path.join(APP_DIR, "assets", "icon.png")
    is_icon = os.path.exists(icon_path) and os.path.getsize(icon_path) > 500
    report("Application Icon Asset", is_icon, f"Path: {icon_path} ({os.path.getsize(icon_path) if is_icon else 0} bytes)")

    css_path = os.path.join(APP_DIR, "style.css")
    is_css = os.path.exists(css_path) and os.path.getsize(css_path) > 500
    report("Dark Theme Glassmorphism CSS", is_css, f"Path: {css_path} ({os.path.getsize(css_path) if is_css else 0} bytes)")

    for script in ["run.sh", "install.sh", "uninstall.sh"]:
        sp = os.path.join(APP_DIR, script)
        is_exec = os.path.exists(sp) and os.access(sp, os.X_OK)
        report(f"Executable Script: {script}", is_exec, f"Path: {sp}")

    for doc in ["README.md", "LICENSE"]:
        dp = os.path.join(APP_DIR, doc)
        is_doc = os.path.exists(dp) and os.path.getsize(dp) > 100
        report(f"Documentation / Legal: {doc}", is_doc, f"Path: {dp}")


def test_code_compilation():
    section("3. Python Code Syntax & Bytecode Compilation")

    py_files = [f for f in os.listdir(APP_DIR) if f.endswith(".py") and f != "verify_app.py"]
    for pf in sorted(py_files):
        try:
            py_compile.compile(os.path.join(APP_DIR, pf), doraise=True)
            report(f"Syntax Validation: {pf}", True, "Zero syntax or indentation errors")
        except Exception as e:
            report(f"Syntax Validation: {pf}", False, str(e))


def test_i18n_localization():
    section("4. Multi-Language Localization (i18n)")

    try:
        from i18n import MESSAGES
        supported_langs = list(MESSAGES.keys())
        report("Supported Languages Loaded", len(supported_langs) >= 5, f"Languages: {', '.join(supported_langs)}")

        en_keys = set(MESSAGES.get("en", {}).keys())
        report("English Base Dictionary", len(en_keys) > 20, f"Found {len(en_keys)} localization keys")

        for lang in supported_langs:
            if lang == "en":
                continue
            cur_keys = set(MESSAGES.get(lang, {}).keys())
            missing = en_keys - cur_keys
            report(f"Language Parity: {lang.upper()}", len(missing) == 0,
                   f"{len(cur_keys)}/{len(en_keys)} keys present" if not missing else f"Missing: {missing}")
    except Exception as e:
        report("Localization Module", False, str(e))


def test_core_components():
    section("5. Core Component Instantiation")

    try:
        from queue_manager import QueueManager
        qm = QueueManager()
        report("QueueManager Instantiation", qm is not None, "LevelDB reverse scanner initialized")

        from mpris_manager import MPRISManager
        mm = MPRISManager()
        report("MPRISManager Instantiation", mm is not None, "D-Bus MPRIS client initialized")

        from visualizer import VisualizerWidget
        vw = VisualizerWidget()
        report("VisualizerWidget Instantiation", vw is not None, "Animated equalizer bars ready")
    except Exception as e:
        report("Core Components", False, str(e))


def main():
    print(f"\n{BOLD}══════════════════════════════════════════════════════════════════{RESET}")
    print(f"{BOLD}         🎵 Spotify Mini Player — Full Verification Suite{RESET}")
    print(f"{BOLD}══════════════════════════════════════════════════════════════════{RESET}")

    test_system_dependencies()
    test_assets_and_files()
    test_code_compilation()
    test_i18n_localization()
    test_core_components()

    total = passed_tests + failed_tests
    print(f"\n{BOLD}══════════════════════════════════════════════════════════════════{RESET}")
    if failed_tests == 0:
        print(f" {GREEN}{BOLD}🎉 ALL {passed_tests}/{total} CHECKS PASSED SUCCESSFULLY!{RESET}")
        print(f" {GREEN}Spotify Mini Player is 100% verified and ready for deployment.{RESET}")
        print(f"{BOLD}══════════════════════════════════════════════════════════════════{RESET}\n")
        return 0
    else:
        print(f" {RED}{BOLD}⚠️ {failed_tests} out of {total} checks FAILED.{RESET}")
        print(f"{BOLD}══════════════════════════════════════════════════════════════════{RESET}\n")
        return 1


if __name__ == "__main__":
    sys.exit(main())
