#!/usr/bin/env python3
import sys
import os
import json
import time
import ctypes
import subprocess
from datetime import timedelta
import random

import gi
gi.require_version('Gtk', '4.0')
gi.require_version('Adw', '1')
gi.require_version('GdkX11', '4.0')
gi.require_version('GdkPixbuf', '2.0')

from gi.repository import Gtk, Adw, Gdk, GdkX11, GdkPixbuf, GLib, Pango, Gio

from mpris_manager import MPRISManager
from visualizer import VisualizerWidget
from queue_manager import QueueManager, is_valid_name
import i18n
from i18n import t

SPOTIFY_LOGO_PATH = "/usr/share/spotify/icons/spotify-linux-128.png"
CONFIG_DIR = os.path.expanduser("~/.config/spotify-mini-player")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

def format_time(seconds):
    if seconds < 0:
        seconds = 0
    td = timedelta(seconds=int(seconds))
    total_secs = int(td.total_seconds())
    mins = total_secs // 60
    secs = total_secs % 60
    return f"{mins:02d}:{secs:02d}"

def is_single_track(title, album):
    if not title or not album:
        return False
    t_clean = title.strip().casefold()
    a_clean = album.strip().casefold()
    return (a_clean == t_clean) or (a_clean in (f"{t_clean} - single", f"{t_clean} (single)"))

def format_artist_and_album(artist, album, title=""):
    artist = (artist or "").strip()
    if artist.casefold() == "spotify":
        artist = ""
    album = (album or "").strip()
    title = (title or "").strip()

    if is_single_track(title, album):
        single_str = t("single")
        return f"{artist} • {single_str}" if artist else single_str

    if artist and album:
        return f"{artist} • {album}"
    return artist or album or ""

def get_active_window():
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        disp = x11.XOpenDisplay(None)
        if not disp:
            return 0
        root = x11.XDefaultRootWindow(disp)
        atom_active = x11.XInternAtom(disp, b'_NET_ACTIVE_WINDOW', False)
        actual_type, actual_format, nitems, bytes_after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        prop = ctypes.c_void_p()
        res = x11.XGetWindowProperty(disp, root, atom_active, 0, 1, False, 33, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(prop))
        win = (ctypes.c_ulong * 1).from_address(prop.value)[0] if (res == 0 and prop.value) else 0
        if prop.value:
            x11.XFree(prop)
        x11.XCloseDisplay(disp)
        return win
    except Exception:
        return 0

def find_spotify_window():
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        disp = x11.XOpenDisplay(None)
        if not disp:
            return 0
        root = x11.XDefaultRootWindow(disp)
        atom_client_list = x11.XInternAtom(disp, b'_NET_CLIENT_LIST', False)
        atom_wm_class = x11.XInternAtom(disp, b'WM_CLASS', False)
        actual_type, actual_format, nitems, bytes_after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        prop = ctypes.c_void_p()
        res = x11.XGetWindowProperty(disp, root, atom_client_list, 0, 1024, False, 33, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(prop))
        found_win = 0
        if res == 0 and prop.value:
            wins = (ctypes.c_ulong * nitems.value).from_address(prop.value)
            for w in wins:
                class_prop = ctypes.c_void_p()
                s = x11.XGetWindowProperty(disp, w, atom_wm_class, 0, 1024, False, 0, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(class_prop))
                if s == 0 and class_prop.value:
                    val = ctypes.string_at(class_prop.value).decode('utf-8', errors='ignore')
                    x11.XFree(class_prop)
                    if 'spotify' in val.lower():
                        found_win = w
                        break
            x11.XFree(prop)
        x11.XCloseDisplay(disp)
        return found_win
    except Exception:
        return 0

def is_window_minimized(w):
    if not w:
        return False
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        disp = x11.XOpenDisplay(None)
        if not disp:
            return False
        atom_wm_state = x11.XInternAtom(disp, b'_NET_WM_STATE', False)
        atom_hidden = x11.XInternAtom(disp, b'_NET_WM_STATE_HIDDEN', False)
        actual_type, actual_format, nitems, bytes_after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        prop = ctypes.c_void_p()
        res = x11.XGetWindowProperty(disp, w, atom_wm_state, 0, 1024, False, 4, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(prop))
        is_min = False
        if res == 0 and prop.value:
            states = (ctypes.c_ulong * nitems.value).from_address(prop.value)
            is_min = (atom_hidden in states)
            x11.XFree(prop)
        x11.XCloseDisplay(disp)
        return is_min
    except Exception:
        return False

def activate_window(w):
    if not w:
        return
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        disp = x11.XOpenDisplay(None)
        if not disp:
            return
        root = x11.XDefaultRootWindow(disp)
        class XClientMessageEvent(ctypes.Structure):
            _fields_ = [
                ('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                ('format', ctypes.c_int), ('data', ctypes.c_long * 5)
            ]
        atom_active = x11.XInternAtom(disp, b'_NET_ACTIVE_WINDOW', False)
        event = XClientMessageEvent(33, 0, 1, disp, w, atom_active, 32, (ctypes.c_long * 5)(2, 0, 0, 0, 0))
        mask = (1 << 19) | (1 << 20)
        x11.XSendEvent(disp, root, False, mask, ctypes.byref(event))
        x11.XFlush(disp)
        x11.XCloseDisplay(disp)
    except Exception:
        pass

def is_spotify_active():
    """Checks if Spotify is currently the focused/active window on X11."""
    try:
        active = get_active_window()
        if not active:
            return False
        sp = find_spotify_window()
        if sp != 0 and active == sp:
            return True
        x11 = ctypes.CDLL('libX11.so.6')
        disp = x11.XOpenDisplay(None)
        if not disp:
            return False
        atom_wm_class = x11.XInternAtom(disp, b'WM_CLASS', False)
        curr = active
        is_sp = False
        for _ in range(5):
            actual_type, actual_format, nitems, bytes_after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
            class_prop = ctypes.c_void_p()
            s = x11.XGetWindowProperty(disp, curr, atom_wm_class, 0, 1024, False, 0, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(class_prop))
            if s == 0 and class_prop.value:
                val = ctypes.string_at(class_prop.value).decode('utf-8', errors='ignore')
                x11.XFree(class_prop)
                if 'spotify' in val.lower():
                    is_sp = True
                    break
            root_ret, parent_ret, children_ret, nchildren = ctypes.c_ulong(), ctypes.c_ulong(), ctypes.c_void_p(), ctypes.c_uint()
            q = x11.XQueryTree(disp, curr, ctypes.byref(root_ret), ctypes.byref(parent_ret), ctypes.byref(children_ret), ctypes.byref(nchildren))
            if children_ret.value:
                x11.XFree(children_ret)
            if q == 0 or parent_ret.value == 0 or parent_ret.value == root_ret.value:
                break
            curr = parent_ret.value
        x11.XCloseDisplay(disp)
        return is_sp
    except Exception:
        return False

def is_spotify_on_screen():
    """Checks if Spotify window is currently restored/visible on the active screen (not minimized)."""
    try:
        sp = find_spotify_window()
        if not sp:
            return False
        if is_window_minimized(sp):
            return False
        x11 = ctypes.CDLL('libX11.so.6')
        disp = x11.XOpenDisplay(None)
        if not disp:
            return False
        root = x11.XDefaultRootWindow(disp)
        atom_cur_desk = x11.XInternAtom(disp, b"_NET_CURRENT_DESKTOP", False)
        atom_wm_desk = x11.XInternAtom(disp, b"_NET_WM_DESKTOP", False)
        actual_type, actual_format, nitems, bytes_after = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_ulong(), ctypes.c_ulong()
        
        prop = ctypes.c_void_p()
        res = x11.XGetWindowProperty(disp, root, atom_cur_desk, 0, 1, False, 0, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(prop))
        cur_desk = (ctypes.c_ulong * 1).from_address(prop.value)[0] if (res == 0 and prop.value) else 0
        if prop.value:
            x11.XFree(prop)

        prop = ctypes.c_void_p()
        res = x11.XGetWindowProperty(disp, sp, atom_wm_desk, 0, 1, False, 0, ctypes.byref(actual_type), ctypes.byref(actual_format), ctypes.byref(nitems), ctypes.byref(bytes_after), ctypes.byref(prop))
        sp_desk = (ctypes.c_ulong * 1).from_address(prop.value)[0] if (res == 0 and prop.value) else 0
        if prop.value:
            x11.XFree(prop)
        
        x11.XCloseDisplay(disp)
        if sp_desk not in (cur_desk, 0xffffffff):
            return False
        return True
    except Exception:
        return False

def get_window_pos(xid):
    """Gets absolute root screen coordinates of window."""
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        disp = x11.XOpenDisplay(None)
        if not disp:
            return None
        root = x11.XDefaultRootWindow(disp)
        child = ctypes.c_ulong()
        x = ctypes.c_int()
        y = ctypes.c_int()
        res = x11.XTranslateCoordinates(disp, xid, root, 0, 0, ctypes.byref(x), ctypes.byref(y), ctypes.byref(child))
        x11.XCloseDisplay(disp)
        if res != 0:
            return (x.value, y.value)
    except Exception:
        pass
    return None

def move_window_to(xid, x, y):
    """Positions window at exact screen coordinates."""
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        disp = x11.XOpenDisplay(None)
        if not disp:
            return
        x11.XMoveWindow(disp, xid, int(x), int(y))
        x11.XFlush(disp)
        x11.XCloseDisplay(disp)
    except Exception as e:
        print(f"Error moving window: {e}")

def is_pointer_over_window(xid, width=None, height=None):
    """Checks if mouse cursor is physically inside the window bounding box."""
    if not xid:
        return False
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        disp = x11.XOpenDisplay(None)
        if not disp:
            return False
        root = x11.XDefaultRootWindow(disp)
        r_root, r_child = ctypes.c_ulong(), ctypes.c_ulong()
        rx, ry, wx, wy = ctypes.c_int(), ctypes.c_int(), ctypes.c_int(), ctypes.c_int()
        mask = ctypes.c_uint()
        res = x11.XQueryPointer(disp, xid, ctypes.byref(r_root), ctypes.byref(r_child),
                                ctypes.byref(rx), ctypes.byref(ry),
                                ctypes.byref(wx), ctypes.byref(wy),
                                ctypes.byref(mask))
        if res == 0:
            x11.XCloseDisplay(disp)
            return False

        if width is not None and height is not None:
            w, h = width, height
        else:
            root_ret, x_ret, y_ret = ctypes.c_ulong(), ctypes.c_int(), ctypes.c_int()
            w_ret, h_ret = ctypes.c_uint(), ctypes.c_uint()
            border_ret, depth_ret = ctypes.c_uint(), ctypes.c_uint()
            geom_res = x11.XGetGeometry(disp, xid, ctypes.byref(root_ret),
                                       ctypes.byref(x_ret), ctypes.byref(y_ret),
                                       ctypes.byref(w_ret), ctypes.byref(h_ret),
                                       ctypes.byref(border_ret), ctypes.byref(depth_ret))
            w = w_ret.value if geom_res != 0 else 380
            h = h_ret.value if geom_res != 0 else 450

        x11.XCloseDisplay(disp)
        return (0 <= wx.value <= w) and (0 <= wy.value <= h)
    except Exception:
        return False

def set_window_always_above(xid, enable=True):
    """Ensures window stays above all other windows using proper EWMH client messages."""
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XDefaultRootWindow.restype = ctypes.c_ulong
        x11.XInternAtom.restype = ctypes.c_ulong
        disp = x11.XOpenDisplay(None)
        if not disp:
            return
        root = x11.XDefaultRootWindow(disp)

        atom_state = x11.XInternAtom(disp, b'_NET_WM_STATE', False)
        atom_above = x11.XInternAtom(disp, b'_NET_WM_STATE_ABOVE', False)
        atom_sticky = x11.XInternAtom(disp, b'_NET_WM_STATE_STICKY', False)
        atom_skip_tb = x11.XInternAtom(disp, b'_NET_WM_STATE_SKIP_TASKBAR', False)
        atom_skip_p = x11.XInternAtom(disp, b'_NET_WM_STATE_SKIP_PAGER', False)

        class XClientMessageEvent(ctypes.Structure):
            _fields_ = [
                ('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                ('format', ctypes.c_int), ('data', ctypes.c_long * 5)
            ]

        action = 1 if enable else 0
        mask = 0x00180000

        e1 = XClientMessageEvent(33, 0, 1, disp, xid, atom_state, 32, (ctypes.c_long * 5)(action, atom_above, atom_sticky, 1, 0))
        x11.XSendEvent(disp, root, False, mask, ctypes.byref(e1))

        e2 = XClientMessageEvent(33, 0, 1, disp, xid, atom_state, 32, (ctypes.c_long * 5)(1, atom_skip_tb, atom_skip_p, 1, 0))
        x11.XSendEvent(disp, root, False, mask, ctypes.byref(e2))

        x11.XFlush(disp)
        x11.XCloseDisplay(disp)
    except Exception as e:
        print(f"Failed to set window always above: {e}")

def set_window_motif_hints(xid):
    """Sets Motif WM hints so window manager allows minimizing undecorated windows."""
    try:
        x11 = ctypes.CDLL('libX11.so.6')
        x11.XOpenDisplay.restype = ctypes.c_void_p
        disp = x11.XOpenDisplay(None)
        if not disp:
            return
        atom_motif = x11.XInternAtom(disp, b"_MOTIF_WM_HINTS", False)
        hints = (ctypes.c_ulong * 5)(3, 1, 0, 0, 0)
        x11.XChangeProperty(disp, xid, atom_motif, atom_motif, 32, 0, ctypes.byref(hints), 5)
        x11.XFlush(disp)
        x11.XCloseDisplay(disp)
    except Exception as e:
        print(f"Failed to set motif hints: {e}")

class SpotifyMiniWindow(Gtk.Window):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self.set_title("Spotify Mini")
        self.set_default_size(400, 185)
        self.set_resizable(False)
        self.set_decorated(False)
        self.set_focusable(False)
        self.add_css_class("mini-player-window")

        self.is_pinned = False
        self.is_hovered = False
        self.is_scrubbing = False
        self.is_queue_open = False
        self.duration_sec = 0
        self.last_track_id = ""

        # Precise anchor-based time progression
        self.anchor_pos = 0.0
        self.anchor_time = time.time()
        self.last_sync_pos = 0.0

        # Volume state
        self.current_volume = 0.7
        self.pre_mute_volume = 0.7

        # Window Position Persistence
        self.saved_x = None
        self.saved_y = None
        self.user_shuffle = False
        self._load_config()

        self._ignore_rewind_until = 0.0
        self.hide_timer_id = None
        self.fade_timer_id = None
        self.seek_timer_id = None
        self.vol_debounce_id = None
        self.shuffle_history = []
        self._switching_track = False
        self._switching_timer_id = None
        self._pending_target_uri = None
        self._skip_target_idx = None
        self._skip_debounce_id = None
        self._was_pinned_before_spotify = False
        self.max_track_pos = 0.0

        # Queue Manager
        self.queue_mgr = QueueManager(on_queue_changed_cb=self._rebuild_queue_ui)

        # MPRIS Manager
        self.mpris = MPRISManager(
            on_update_cb=self._on_metadata_updated,
            on_status_cb=self._on_status_updated,
            on_avail_cb=self._on_availability_changed,
            on_media_key_cb=self._on_user_media_action,
            on_volume_cb=self._on_spotify_volume_changed,
            on_options_cb=self._on_mpris_options_changed,
            on_seeked_cb=self._on_mpris_seeked
        )

        self._build_ui()
        self._setup_events()
        self._setup_timers()

        # Initial sync
        self._apply_metadata(track_changed=True)
        self._sync_initial_volume()
        self._update_playback_options_ui()
        self._rebuild_queue_ui()

        # Show initially for 4.5s
        self.show_osd(duration_ms=4500)

    def _load_config(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        if os.path.exists(CONFIG_FILE):
            try:
                with open(CONFIG_FILE, "r") as f:
                    data = json.load(f)
                    self.saved_x = data.get("x")
                    self.saved_y = data.get("y")
                    self.is_pinned = False  # Reset pin state on restart as requested
                    self.user_shuffle = data.get("user_shuffle", False)
            except Exception as e:
                print(f"Failed to load config: {e}")

    def _save_config(self):
        try:
            data = {
                "x": self.saved_x,
                "y": self.saved_y,
                "pinned": False,  # Always reset pin state across restarts
                "user_shuffle": getattr(self, "user_shuffle", False)
            }
            with open(CONFIG_FILE, "w") as f:
                json.dump(data, f)
        except Exception as e:
            print(f"Failed to save config: {e}")

    def _build_ui(self):
        self.handle = Gtk.WindowHandle()
        self.set_child(self.handle)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.CROSSFADE)
        self.handle.set_child(self.stack)

        # 1. Player Card (Vertical Box)
        self.player_card = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        self.player_card.add_css_class("mini-player-card")
        self.stack.add_named(self.player_card, "player")

        # Top Bar: Real Spotify Logo + Equalizer + Real Volume Slider on Left, Controls on Right
        top_bar = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        top_bar.add_css_class("top-bar")

        # Left box: Logo + Soundwave visualizer + Real Spotify Volume
        left_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        left_box.set_valign(Gtk.Align.CENTER)

        if os.path.exists(SPOTIFY_LOGO_PATH):
            logo_pic = Gtk.Picture.new_for_filename(SPOTIFY_LOGO_PATH)
            logo_pic.set_size_request(18, 18)
            logo_pic.set_can_shrink(True)
            logo_pic.add_css_class("spotify-logo")
            left_box.append(logo_pic)

        self.visualizer = VisualizerWidget(num_bars=5)
        left_box.append(self.visualizer)

        # REAL Spotify Volume Control (Directly next to frequency visualizer!)
        vol_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        vol_box.add_css_class("vol-box")
        vol_box.set_valign(Gtk.Align.CENTER)

        self.vol_btn = Gtk.Button.new_from_icon_name("audio-volume-high-symbolic")
        self.vol_btn.add_css_class("vol-btn")
        self.vol_btn.set_tooltip_text(t("mute_unmute"))
        self.vol_btn.connect("clicked", self._toggle_mute)
        vol_box.append(self.vol_btn)

        self.vol_scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.vol_scale.set_draw_value(False)
        self.vol_scale.add_css_class("vol-scale")
        self.vol_scale.set_size_request(64, 16)
        self.vol_scale.set_hexpand(False)
        self.vol_scale.set_value(70)
        self.vol_scale.set_tooltip_text(t("spotify_volume"))
        self.vol_scale.connect("change-value", self._on_volume_scale_change)
        vol_box.append(self.vol_scale)

        vol_scroll = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        vol_scroll.connect("scroll", self._on_vol_box_scroll)
        vol_box.add_controller(vol_scroll)

        vol_click = Gtk.GestureClick()
        vol_box.add_controller(vol_click)

        left_box.append(vol_box)
        top_bar.append(left_box)

        # Spacer (Grabbable drag area)
        spacer = Gtk.Box()
        spacer.set_hexpand(True)
        top_bar.append(spacer)

        # Right Action Buttons (Queue, Pin, Raise, Close)
        right_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        right_box.set_valign(Gtk.Align.CENTER)

        # Queue Dropdown toggle button
        self.queue_btn = Gtk.Button.new_from_icon_name("view-list-symbolic")
        self.queue_btn.add_css_class("icon-btn")
        self.queue_btn.set_tooltip_text(t("queue_tooltip"))
        self.queue_btn.connect("clicked", self._toggle_queue)
        right_box.append(self.queue_btn)

        # Pin button
        self.pin_btn = Gtk.Button.new_from_icon_name("view-pin-symbolic")
        self.pin_btn.add_css_class("icon-btn")
        if self.is_pinned:
            self.pin_btn.add_css_class("pinned-active")
            self.pin_btn.set_tooltip_text(t("pin_on"))
        else:
            self.pin_btn.set_tooltip_text(t("pin_off"))
        self.pin_btn.connect("clicked", self._toggle_pin)
        right_box.append(self.pin_btn)

        # Raise Spotify
        self.raise_btn = Gtk.Button.new_from_icon_name("external-link-symbolic")
        self.raise_btn.add_css_class("icon-btn")
        self.raise_btn.set_tooltip_text(t("open_spotify"))
        self.raise_btn.connect("clicked", lambda b: self.mpris.raise_spotify())
        right_box.append(self.raise_btn)

        # Minimize button
        self.min_btn = Gtk.Button.new_from_icon_name("window-minimize-symbolic")
        self.min_btn.add_css_class("icon-btn")
        self.min_btn.set_tooltip_text(t("minimize_player"))
        self.min_btn.connect("clicked", lambda b: self._on_minimize_clicked())
        right_box.append(self.min_btn)

        # Close button
        self.close_btn = Gtk.Button.new_from_icon_name("window-close-symbolic")
        self.close_btn.add_css_class("icon-btn")
        self.close_btn.set_tooltip_text(t("close_player"))
        self.close_btn.connect("clicked", lambda b: self._on_close_clicked())
        right_box.append(self.close_btn)

        top_bar.append(right_box)
        self.player_card.append(top_bar)

        # Main Row (Cover Art + Info / Controls)
        main_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        main_row.set_valign(Gtk.Align.START)

        # Cover Art Frame
        self.cover_box = Gtk.Box()
        self.cover_box.add_css_class("cover-art-box")
        self.cover_box.set_size_request(84, 84)
        self.cover_box.set_halign(Gtk.Align.START)
        self.cover_box.set_valign(Gtk.Align.START)
        self.cover_box.set_hexpand(False)
        self.cover_box.set_vexpand(False)
        self.cover_box.set_tooltip_text(t("focus_spotify"))

        cover_click = Gtk.GestureClick()
        cover_click.connect("released", lambda g, n, x, y: self.mpris.raise_spotify())
        self.cover_box.add_controller(cover_click)

        self.cover_pic = Gtk.Picture()
        self.cover_pic.set_can_shrink(True)
        self.cover_pic.set_content_fit(Gtk.ContentFit.COVER)
        self.cover_pic.add_css_class("cover-picture")
        self.cover_pic.set_size_request(84, 84)
        self.cover_pic.set_hexpand(False)
        self.cover_pic.set_vexpand(False)
        self.cover_box.append(self.cover_pic)
        main_row.append(self.cover_box)

        # Right Column
        right_col = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        right_col.set_hexpand(True)
        right_col.set_valign(Gtk.Align.START)
        main_row.append(right_col)

        # Track Title & Artist
        self.title_label = Gtk.Label(label="No Track Playing")
        self.title_label.set_xalign(0.0)
        self.title_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.title_label.add_css_class("track-title")
        right_col.append(self.title_label)

        self.artist_label = Gtk.Label(label="")
        self.artist_label.set_xalign(0.0)
        self.artist_label.set_ellipsize(Pango.EllipsizeMode.END)
        self.artist_label.add_css_class("track-artist")
        right_col.append(self.artist_label)

        # Seek Bar & Time
        slider_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=1)
        slider_box.set_margin_top(2)

        self.scale = Gtk.Scale.new_with_range(Gtk.Orientation.HORIZONTAL, 0, 100, 1)
        self.scale.set_draw_value(False)
        self.scale.set_hexpand(True)
        self.scale.add_css_class("seek-bar")
        self.scale.connect("change-value", self._on_scale_change_value)
        slider_box.append(self.scale)

        # Time row
        time_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        self.pos_label = Gtk.Label(label="00:00")
        self.pos_label.add_css_class("time-label")
        self.pos_label.set_xalign(0.0)

        self.dur_label = Gtk.Label(label="00:00")
        self.dur_label.add_css_class("time-label")
        self.dur_label.set_xalign(1.0)
        self.dur_label.set_hexpand(True)

        time_row.append(self.pos_label)
        time_row.append(self.dur_label)
        slider_box.append(time_row)
        right_col.append(slider_box)

        # Media Control Buttons
        controls_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        controls_box.set_halign(Gtk.Align.CENTER)
        controls_box.set_margin_top(2)

        # Shuffle
        self.shuffle_btn = Gtk.Button.new_from_icon_name("media-playlist-shuffle-symbolic")
        self.shuffle_btn.add_css_class("control-btn")
        self.shuffle_btn.set_tooltip_text(t("shuffle_disabled"))
        self.shuffle_btn.connect("clicked", self._on_shuffle_clicked)
        controls_box.append(self.shuffle_btn)

        # Prev
        self.prev_btn = Gtk.Button.new_from_icon_name("media-skip-backward-symbolic")
        self.prev_btn.add_css_class("control-btn")
        self.prev_btn.set_tooltip_text(t("prev_track"))
        self.prev_btn.connect("clicked", self.on_user_prev_clicked)
        controls_box.append(self.prev_btn)

        # Play / Pause
        self.play_btn = Gtk.Button.new_from_icon_name("media-playback-start-symbolic")
        self.play_btn.add_css_class("play-btn")
        self.play_btn.set_tooltip_text(t("play_pause"))
        self.play_btn.connect("clicked", lambda b: self.mpris.play_pause())
        controls_box.append(self.play_btn)

        # Next
        self.next_btn = Gtk.Button.new_from_icon_name("media-skip-forward-symbolic")
        self.next_btn.add_css_class("control-btn")
        self.next_btn.set_tooltip_text(t("next_track"))
        self.next_btn.connect("clicked", self.on_user_next_clicked)
        controls_box.append(self.next_btn)

        # Repeat (3 modes)
        self.repeat_btn = Gtk.Button.new_from_icon_name("media-playlist-repeat-symbolic")
        self.repeat_btn.add_css_class("control-btn")
        self.repeat_btn.set_tooltip_text(t("repeat_none"))
        self.repeat_btn.connect("clicked", self._on_repeat_clicked)
        controls_box.append(self.repeat_btn)

        right_col.append(controls_box)
        self.player_card.append(main_row)

        # =========================================================================
        # 3. Queue Drawer (Дропбокс очереди с анимацией раскрытия вниз)
        # =========================================================================
        self.queue_revealer = Gtk.Revealer()
        self.queue_revealer.set_transition_type(Gtk.RevealerTransitionType.SLIDE_DOWN)
        self.queue_revealer.set_transition_duration(180)
        self.queue_revealer.connect("notify::child-revealed", self._on_revealer_revealed_changed)

        queue_panel = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        queue_panel.add_css_class("queue-panel")

        # Queue panel header
        q_header = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        self.queue_header_label = Gtk.Label(label=t("queue_title"))
        self.queue_header_label.add_css_class("queue-title")
        self.queue_header_label.set_hexpand(True)
        self.queue_header_label.set_halign(Gtk.Align.START)
        self.queue_header_label.set_xalign(0.0)
        self.queue_header_label.set_ellipsize(Pango.EllipsizeMode.NONE)
        self.queue_header_label.set_wrap(False)
        q_header.append(self.queue_header_label)

        # Queue header spinner indicator
        self.queue_spinner = Gtk.Spinner()
        self.queue_spinner.add_css_class("queue-header-spinner")
        self.queue_spinner.set_size_request(16, 16)
        self.queue_spinner.set_valign(Gtk.Align.CENTER)
        self.queue_spinner.set_visible(False)
        q_header.append(self.queue_spinner)

        # Manual queue refresh button
        self.q_refresh_btn = Gtk.Button.new_from_icon_name("view-refresh-symbolic")
        self.q_refresh_btn.add_css_class("icon-btn")
        self.q_refresh_btn.set_tooltip_text(t("refresh_queue"))
        self.q_refresh_btn.connect("clicked", self._on_user_refresh_queue_clicked)
        q_header.append(self.q_refresh_btn)

        self.q_close_btn = Gtk.Button.new_from_icon_name("pan-up-symbolic")
        self.q_close_btn.add_css_class("icon-btn")
        self.q_close_btn.set_tooltip_text(t("collapse_queue"))
        self.q_close_btn.connect("clicked", lambda b: self._toggle_queue(None))
        q_header.append(self.q_close_btn)

        queue_panel.append(q_header)

        # Scrollable tracklist
        self.queue_scroll = Gtk.ScrolledWindow()
        self.queue_scroll.add_css_class("queue-scroll")
        self.queue_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.queue_scroll.set_min_content_height(190)
        self.queue_scroll.set_propagate_natural_height(False)

        self.is_queue_hovered = False
        self._user_scrolled_queue = False
        self._ignore_scroll_event = False

        q_motion = Gtk.EventControllerMotion()
        q_motion.connect("enter", lambda c, x, y: setattr(self, "is_queue_hovered", True))
        q_motion.connect("leave", lambda c: setattr(self, "is_queue_hovered", False))
        self.queue_scroll.add_controller(q_motion)

        vadj = self.queue_scroll.get_vadjustment()
        if vadj:
            vadj.connect("value-changed", self._on_queue_scrolled)

        self.queue_list_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self.queue_scroll.set_child(self.queue_list_box)

        queue_panel.append(self.queue_scroll)
        self._hide_loading_timer = None

        self.queue_revealer.set_child(queue_panel)
        self.player_card.append(self.queue_revealer)

        # 4. Offline Card
        offline_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        offline_box.add_css_class("offline-card")
        offline_box.set_valign(Gtk.Align.CENTER)
        offline_box.set_halign(Gtk.Align.CENTER)

        self.off_title = Gtk.Label(label=t("offline_title"))
        self.off_title.add_css_class("track-title")
        offline_box.append(self.off_title)

        self.launch_btn = Gtk.Button(label=t("launch_spotify"))
        self.launch_btn.add_css_class("launch-btn")
        self.launch_btn.connect("clicked", self._launch_spotify)
        offline_box.append(self.launch_btn)

        self.stack.add_named(offline_box, "offline")

    def _setup_events(self):
        self.connect("close-request", self._on_close_request)
        self.connect("realize", self._on_realize)

        motion = Gtk.EventControllerMotion()
        motion.connect("enter", self._on_mouse_enter)
        motion.connect("leave", self._on_mouse_leave)
        self.add_controller(motion)

        scroll_ctrl = Gtk.EventControllerScroll.new(Gtk.EventControllerScrollFlags.VERTICAL)
        scroll_ctrl.connect("scroll", self._on_scroll_volume)
        self.add_controller(scroll_ctrl)

        key_ctrl = Gtk.EventControllerKey()
        key_ctrl.connect("key-pressed", self._on_key_pressed)
        self.add_controller(key_ctrl)

    def _on_realize(self, widget):
        surface = self.get_surface()
        if isinstance(surface, GdkX11.X11Surface):
            xid = surface.get_xid()
            set_window_motif_hints(xid)
        if surface and isinstance(surface, Gdk.Toplevel):
            surface.connect("notify::state", self._on_surface_state_changed)

    def _on_surface_state_changed(self, surface, pspec):
        state = surface.get_state()
        if state & Gdk.ToplevelState.MINIMIZED:
            if self.is_pinned:
                self.set_pinned(False)

    def _setup_timers(self):
        GLib.timeout_add(16, self._on_tick)
        GLib.timeout_add(200, self._on_fast_sync)
        GLib.timeout_add(200, self._on_poll_window_state)
        GLib.timeout_add(4000, self._on_periodic_queue_check)

    def _on_periodic_queue_check(self):
        if getattr(self, "_switching_track", False) or getattr(self, "_skip_target_idx", None) is not None:
            return True
        if self.mpris.is_available:
            self.queue_mgr.check_for_updates()
        return True

    def _sync_initial_volume(self):
        vol = self.mpris.get_volume()
        self.current_volume = vol
        self.vol_scale.set_value(vol * 100)
        self._update_volume_icon(vol)

    def _update_volume_icon(self, vol):
        if vol <= 0.01:
            icon = "audio-volume-muted-symbolic"
        elif vol < 0.33:
            icon = "audio-volume-low-symbolic"
        elif vol < 0.66:
            icon = "audio-volume-medium-symbolic"
        else:
            icon = "audio-volume-high-symbolic"
        self.vol_btn.set_icon_name(icon)
        vol_str = t("volume", vol=int(round(vol * 100)))
        self.vol_btn.set_tooltip_text(vol_str)
        self.vol_scale.set_tooltip_text(vol_str)

    def _on_volume_scale_change(self, scale, scroll, value):
        self.is_scrubbing = True
        val = value / 100.0
        self.current_volume = val
        self._update_volume_icon(val)

        if self.vol_debounce_id:
            GLib.source_remove(self.vol_debounce_id)

        def do_set_vol():
            self.mpris.set_volume(val)
            self.vol_debounce_id = None
            GLib.timeout_add(150, self._clear_scrubbing)
            return False

        self.vol_debounce_id = GLib.timeout_add(40, do_set_vol)
        return False

    def _toggle_mute(self, button):
        if self.current_volume > 0.02:
            self.pre_mute_volume = self.current_volume
            self.current_volume = 0.0
        else:
            self.current_volume = self.pre_mute_volume if self.pre_mute_volume > 0.1 else 0.7

        self.vol_scale.set_value(self.current_volume * 100)
        self.mpris.set_volume(self.current_volume)
        self._update_volume_icon(self.current_volume)

    def _on_spotify_volume_changed(self, new_vol):
        """Called whenever volume is changed in Spotify app in real time."""
        GLib.idle_add(self._apply_spotify_volume, new_vol)

    def _apply_spotify_volume(self, new_vol):
        self.current_volume = new_vol
        self.vol_scale.set_value(new_vol * 100)
        self._update_volume_icon(new_vol)
        return False

    def _on_revealer_revealed_changed(self, revealer, param):
        if not revealer.get_child_revealed() and not self.is_queue_open:
            self.set_default_size(400, 185)

    def _on_queue_scrolled(self, adj):
        if getattr(self, "_ignore_scroll_event", False):
            return
        if getattr(self, "is_queue_hovered", False):
            self._user_scrolled_queue = True

    def _scroll_to_current_track(self):
        if not hasattr(self, "queue_scroll") or not self.queue_scroll:
            return False
        adj = self.queue_scroll.get_vadjustment()
        if not adj:
            return False

        viewport_h = adj.get_page_size()
        if viewport_h <= 10.0:
            viewport_h = 190.0

        target_y = None
        if hasattr(self, "curr_row") and self.curr_row and hasattr(self, "queue_list_box") and self.queue_list_box:
            try:
                ok, rect = self.curr_row.compute_bounds(self.queue_list_box)
                if ok and rect.size.height > 0:
                    row_center = rect.origin.y + (rect.size.height / 2.0)
                    target_y = row_center - (viewport_h / 2.0)
            except Exception:
                target_y = None

        if target_y is None:
            is_loop = getattr(self.mpris, "loop_status", "Playlist") != "None"
            past_tracks = self.queue_mgr.get_past_tracks(limit=40, loop=is_loop)
            cur_top = (26.0 + len(past_tracks) * 36.0 + 26.0) if past_tracks else 26.0
            cur_h = 42.0
            target_y = cur_top + (cur_h / 2.0) - (viewport_h / 2.0)

        upper = adj.get_upper()
        if upper > viewport_h:
            max_scroll = upper - viewport_h
            final_y = max(0.0, min(target_y, max_scroll))
        else:
            final_y = max(0.0, target_y)

        self._ignore_scroll_event = True
        try:
            adj.set_value(final_y)
        finally:
            self._ignore_scroll_event = False
        return False

    def _toggle_queue(self, button=None):
        self.is_queue_open = not self.queue_revealer.get_reveal_child()
        if self.is_queue_open:
            self._user_scrolled_queue = False
            self.queue_btn.add_css_class("queue-active")
            self._cancel_hide_timer()
            self._cancel_fade()
            self.set_opacity(1.0)
            self.queue_mgr.check_for_updates()
            self._rebuild_queue_ui(order_changed=False)
            self.queue_revealer.set_reveal_child(True)
            GLib.idle_add(self._scroll_to_current_track)
            GLib.timeout_add(60, self._scroll_to_current_track)
            GLib.timeout_add(180, self._scroll_to_current_track)
            GLib.timeout_add(320, self._scroll_to_current_track)
            if not self.is_pinned and not self.is_hovered:
                self._schedule_hide(4500)
        else:
            self.hide_queue_loading()
            self.queue_btn.remove_css_class("queue-active")
            self.queue_revealer.set_reveal_child(False)
            if not self.is_pinned and not self.is_hovered:
                self._schedule_hide(3500)

    def _on_user_refresh_queue_clicked(self, *args):
        self.show_queue_loading(duration_ms=1500)
        self.show_osd(4500, force=True)

        def _do_refresh():
            try:
                self.queue_mgr.force_refresh_context(
                    title=self.mpris.title,
                    artist=self.mpris.artist,
                    uri=self.mpris.track_id,
                    album=self.mpris.album
                )
            except Exception as e:
                print(f"Error during manual queue refresh: {e}")
            GLib.idle_add(lambda: self._rebuild_queue_ui(order_changed=True))
            GLib.timeout_add(120, self._scroll_to_current_track)

        t = threading.Thread(target=_do_refresh, daemon=True)
        t.start()

    def _set_switching_track(self, uri=None, duration_ms=3500):
        if getattr(self, "_switching_timer_id", None):
            try:
                GLib.source_remove(self._switching_timer_id)
            except Exception:
                pass
            self._switching_timer_id = None

        self._switching_track = True
        if uri:
            self._pending_target_uri = uri

        def _clear_switching():
            self._switching_track = False
            self._pending_target_uri = None
            self._skip_target_idx = None
            self._switching_timer_id = None
            self._apply_metadata(track_changed=False)
            return False

        self._switching_timer_id = GLib.timeout_add(duration_ms, _clear_switching)

    def on_user_next_clicked(self, *args):
        all_tracks = self.queue_mgr.all_context_tracks
        if not all_tracks or len(all_tracks) <= 1:
            self._set_switching_track(duration_ms=3500)
            self._ignore_rewind_until = time.time() + 1.5
            self.max_track_pos = 0.0
            self.anchor_pos = 0.0
            self.anchor_time = time.time()
            self.last_sync_pos = 0.0
            self.scale.set_value(0)
            self.pos_label.set_text("00:00")
            self.mpris.next()
            self.show_osd(4500, force=True)
            return

        is_shuffle = getattr(self, "user_shuffle", False)
        if is_shuffle:
            if self._skip_target_idx is not None and 0 <= self._skip_target_idx < len(all_tracks):
                curr_track = all_tracks[self._skip_target_idx]
                curr_uri = curr_track.get("uri", "")
            else:
                curr_uri = self.mpris.track_id or (self.queue_mgr.current_track.get("uri") if self.queue_mgr.current_track else "")
            curr_id = self.queue_mgr._extract_id(curr_uri)

            if curr_uri:
                if not hasattr(self, "shuffle_history") or self.shuffle_history is None:
                    self.shuffle_history = []
                if not self.shuffle_history or self.shuffle_history[-1] != curr_uri:
                    self.shuffle_history.append(curr_uri)
                    if len(self.shuffle_history) > 100:
                        self.shuffle_history.pop(0)

            played_ids = {self.queue_mgr._extract_id(u) for u in getattr(self, "shuffle_history", [])}
            candidates = [t for t in all_tracks if self.queue_mgr._extract_id(t.get("uri")) != curr_id and self.queue_mgr._extract_id(t.get("uri")) not in played_ids]

            if not candidates:
                candidates = [t for t in all_tracks if self.queue_mgr._extract_id(t.get("uri")) != curr_id]
                self.shuffle_history = [curr_uri] if curr_uri else []

            chosen = random.choice(candidates) if candidates else all_tracks[0]
            target_idx = self.queue_mgr._find_track_idx(chosen.get("uri", ""), chosen.get("title", ""))
            self._skip_target_idx = target_idx if target_idx >= 0 else 0
            self.queue_mgr.last_valid_idx = self._skip_target_idx
            target_track = chosen
        else:
            total = len(all_tracks)
            if self._skip_target_idx is not None and 0 <= self._skip_target_idx < total:
                base_idx = self._skip_target_idx
            else:
                matched_idx = self.queue_mgr._find_track_idx(self.mpris.track_id, self.mpris.title)
                if matched_idx >= 0:
                    base_idx = matched_idx
                else:
                    base_idx = getattr(self.queue_mgr, "last_valid_idx", 0)
                if base_idx < 0 or base_idx >= total:
                    base_idx = 0

            is_loop = getattr(self.mpris, "loop_status", "Playlist") != "None"
            if base_idx + 1 < total:
                next_idx = base_idx + 1
            else:
                if not is_loop:
                    self.mpris.pause()
                    self.anchor_pos = self.duration_sec
                    self.scale.set_value(self.duration_sec)
                    self.pos_label.set_text(format_time(self.duration_sec))
                    return
                next_idx = 0

            self._skip_target_idx = next_idx
            self.queue_mgr.last_valid_idx = next_idx
            target_track = all_tracks[next_idx]

        self._apply_immediate_skip_ui_and_debounce(target_track)
        self.show_osd(4500, force=True)

    def on_user_prev_clicked(self, *args):
        fresh_us = self.mpris.get_fresh_position()
        if fresh_us > 3_000_000 and self._skip_target_idx is None:
            self._ignore_rewind_until = time.time() + 1.5
            self.mpris.previous()
            self.anchor_pos = 0.0
            self.anchor_time = time.time()
            self.scale.set_value(0)
            self.pos_label.set_text("00:00")
            self.show_osd(4500, force=True)
            return

        all_tracks = self.queue_mgr.all_context_tracks
        if not all_tracks or len(all_tracks) <= 1:
            self._set_switching_track(duration_ms=3500)
            self._ignore_rewind_until = time.time() + 1.5
            self.anchor_pos = 0.0
            self.anchor_time = time.time()
            self.last_sync_pos = 0.0
            self.scale.set_value(0)
            self.pos_label.set_text("00:00")
            self.mpris.previous()
            self.show_osd(4500, force=True)
            return

        is_shuffle = getattr(self, "user_shuffle", False)
        if is_shuffle:
            if hasattr(self, "shuffle_history") and self.shuffle_history:
                curr_uri = self.mpris.track_id or (self.queue_mgr.current_track.get("uri") if self.queue_mgr.current_track else "")
                curr_id = self.queue_mgr._extract_id(curr_uri)
                while self.shuffle_history and self.queue_mgr._extract_id(self.shuffle_history[-1]) == curr_id:
                    self.shuffle_history.pop()

                if self.shuffle_history:
                    prev_uri = self.shuffle_history.pop()
                    target_idx = self.queue_mgr._find_track_idx(prev_uri)
                    if target_idx >= 0:
                        self._skip_target_idx = target_idx
                        self.queue_mgr.last_valid_idx = target_idx
                        self._apply_immediate_skip_ui_and_debounce(all_tracks[target_idx])
                        self.show_osd(4500, force=True)
                        return

            past = self.queue_mgr.get_past_tracks(limit=10, loop=True)
            chosen = past[-1] if past else all_tracks[-1]
            target_idx = self.queue_mgr._find_track_idx(chosen.get("uri", ""), chosen.get("title", ""))
            self._skip_target_idx = target_idx if target_idx >= 0 else len(all_tracks) - 1
            self.queue_mgr.last_valid_idx = self._skip_target_idx
            target_track = chosen
        else:
            total = len(all_tracks)
            if self._skip_target_idx is not None and 0 <= self._skip_target_idx < total:
                base_idx = self._skip_target_idx
            else:
                matched_idx = self.queue_mgr._find_track_idx(self.mpris.track_id, self.mpris.title)
                if matched_idx >= 0:
                    base_idx = matched_idx
                else:
                    base_idx = getattr(self.queue_mgr, "last_valid_idx", 0)
                if base_idx < 0 or base_idx >= total:
                    base_idx = 0

            is_loop = getattr(self.mpris, "loop_status", "Playlist") != "None"
            if base_idx - 1 >= 0:
                prev_idx = base_idx - 1
            else:
                prev_idx = (total - 1) if is_loop else 0

            self._skip_target_idx = prev_idx
            self.queue_mgr.last_valid_idx = prev_idx
            target_track = all_tracks[prev_idx]

        self._apply_immediate_skip_ui_and_debounce(target_track)
        self.show_osd(4500, force=True)

    def _apply_immediate_skip_ui_and_debounce(self, target_track):
        target_uri = target_track.get("uri", "")
        self._set_switching_track(target_uri, duration_ms=3500)

        # Immediately update local queue state and UI labels
        self.queue_mgr.current_track = dict(target_track)
        if self._skip_target_idx is not None:
            self.queue_mgr.last_valid_idx = self._skip_target_idx
            self.queue_mgr.current_track["track_num"] = self._skip_target_idx + 1
        elif "track_num" not in self.queue_mgr.current_track or not self.queue_mgr.current_track.get("track_num"):
            self.queue_mgr.current_track["track_num"] = getattr(self.queue_mgr, "last_valid_idx", 0) + 1

        title = target_track.get("title") or t("track_default")
        if title in ("Трек", "", None, "Track"):
            tid = self.queue_mgr._extract_id(target_uri)
            if tid and tid in self.queue_mgr.track_meta_cache:
                meta = self.queue_mgr.track_meta_cache[tid]
                if meta.get("title"):
                    title = meta["title"]
                    self.queue_mgr.current_track["title"] = title
        self.title_label.set_text(title)
        self.title_label.set_tooltip_text(title)

        artist = (target_track.get("artist") or "").strip()
        if not artist or artist.casefold() == "spotify":
            tid = self.queue_mgr._extract_id(target_uri)
            if tid and tid in self.queue_mgr.track_meta_cache:
                meta = self.queue_mgr.track_meta_cache[tid]
                if meta.get("artist") and meta["artist"].casefold() != "spotify":
                    artist = meta["artist"]
                    self.queue_mgr.current_track["artist"] = artist
        if artist.casefold() == "spotify":
            artist = ""
        album = target_track.get("album") or ""
        artist_text = format_artist_and_album(artist, album, target_track.get("title") or "")
        self.artist_label.set_text(artist_text)
        self.artist_label.set_tooltip_text(artist_text)

        self.max_track_pos = 0.0
        self.anchor_pos = 0.0
        self.anchor_time = time.time()
        self.last_sync_pos = 0.0
        self.scale.set_value(0)
        self.pos_label.set_text("00:00")
        self._ignore_rewind_until = time.time() + 2.0

        # Show queue spinner when track is skipped (if queue is open)
        if getattr(self, "is_queue_open", False):
            self.show_queue_loading(duration_ms=1500)

        # Smoothly update queue UI
        self._rebuild_queue_ui(order_changed=False)

        # Debounce the actual OpenUri call to Spotify by 300ms:
        if getattr(self, "_skip_debounce_id", None):
            try:
                GLib.source_remove(self._skip_debounce_id)
            except Exception:
                pass
            self._skip_debounce_id = None

        def do_send_open_uri():
            self._skip_debounce_id = None
            self.play_track_silent(target_uri)
            return False

        self._skip_debounce_id = GLib.timeout_add(300, do_send_open_uri)

    def play_track_silent(self, uri):
        if not uri:
            return

        self._set_switching_track(uri, duration_ms=3500)

        # Normalize URI format
        if uri.startswith("/com/spotify/track/"):
            uri = "spotify:track:" + uri.split("/")[-1]
        elif "open.spotify.com/track/" in uri:
            tid = uri.split("track/")[-1].split("?")[0]
            uri = f"spotify:track:{tid}"

        # Reject search or invalid URIs to prevent Spotify's "Песня не найдена" modal dialog
        if not uri.startswith("spotify:track:"):
            print(f"Skipping invalid track URI: {uri}")
            return

        # Instantly update mini player current track if clicked from active context
        if self.queue_mgr.all_context_tracks:
            clean_tid = self.queue_mgr._extract_id(uri)
            for idx, t in enumerate(self.queue_mgr.all_context_tracks):
                if t.get("uri") == uri or self.queue_mgr._extract_id(t.get("uri")) == clean_tid:
                    self.queue_mgr.current_track = dict(t)
                    self.queue_mgr.current_track["track_num"] = idx + 1
                    self.queue_mgr.last_valid_idx = idx
                    self._skip_target_idx = idx
                    self._rebuild_queue_ui()
                    title = t.get("title")
                    if title:
                        self.title_label.set_text(title)
                        self.title_label.set_tooltip_text(title)
                    artist = (t.get("artist") or "").strip()
                    if artist.casefold() == "spotify":
                        artist = ""
                    album = t.get("album") or ""
                    artist_text = format_artist_and_album(artist, album, t.get("title") or "")
                    if artist_text:
                        self.artist_label.set_text(artist_text)
                        self.artist_label.set_tooltip_text(artist_text)
                    self.max_track_pos = 0.0
                    self.anchor_pos = 0.0
                    self.anchor_time = time.time()
                    self.last_sync_pos = 0.0
                    self.scale.set_value(0)
                    self.pos_label.set_text("00:00")
                    self._ignore_rewind_until = time.time() + 2.0
                    break

        active_win = get_active_window()
        sp_win = find_spotify_window()
        sp_was_active = (sp_win != 0 and active_win == sp_win)
        sp_was_minimized = is_window_minimized(sp_win) if sp_win else True

        # CRITICAL: If Spotify is in the background, make it 100% transparent and lower it BEFORE calling open_uri
        # This completely prevents Spotify's window from flashing on screen.
        if not sp_was_active and sp_win:
            try:
                x11 = ctypes.CDLL('libX11.so.6')
                disp = x11.XOpenDisplay(None)
                if disp:
                    atom_opacity = x11.XInternAtom(disp, b'_NET_WM_WINDOW_OPACITY', False)
                    opacity_zero = ctypes.c_ulong(0)
                    x11.XChangeProperty(disp, sp_win, atom_opacity, 6, 32, 0, ctypes.byref(opacity_zero), 1)
                    x11.XLowerWindow(disp, sp_win)
                    x11.XFlush(disp)
                    x11.XCloseDisplay(disp)
            except Exception as e:
                print(f"Error making Spotify transparent: {e}")

        # Open track via MPRIS
        self.mpris.open_uri(uri)

        # Ensure mini player stays strictly above and visible
        surface = self.get_surface()
        if isinstance(surface, GdkX11.X11Surface):
            my_xid = surface.get_xid()
            set_window_always_above(my_xid, True)

        # Streamlined Spotify window suppression
        if not sp_was_active and sp_win:
            def _suppress():
                try:
                    x11 = ctypes.CDLL('libX11.so.6')
                    disp = x11.XOpenDisplay(None)
                    if disp:
                        if sp_was_minimized:
                            x11.XIconifyWindow(disp, sp_win, 0)
                        else:
                            x11.XLowerWindow(disp, sp_win)
                        if isinstance(surface, GdkX11.X11Surface):
                            x11.XRaiseWindow(disp, surface.get_xid())
                        if active_win and active_win != sp_win:
                            root = x11.XDefaultRootWindow(disp)
                            class XClientMessageEvent(ctypes.Structure):
                                _fields_ = [
                                    ('type', ctypes.c_int), ('serial', ctypes.c_ulong), ('send_event', ctypes.c_int),
                                    ('display', ctypes.c_void_p), ('window', ctypes.c_ulong), ('message_type', ctypes.c_ulong),
                                    ('format', ctypes.c_int), ('data', ctypes.c_long * 5)
                                ]
                            atom_active = x11.XInternAtom(disp, b'_NET_ACTIVE_WINDOW', False)
                            event = XClientMessageEvent(33, 0, 1, disp, active_win, atom_active, 32, (ctypes.c_long * 5)(2, 0, 0, 0, 0))
                            mask = (1 << 19) | (1 << 20)
                            x11.XSendEvent(disp, root, False, mask, ctypes.byref(event))
                        x11.XFlush(disp)
                        x11.XCloseDisplay(disp)
                except Exception as e:
                    print(f"Error suppressing Spotify: {e}")
                return False

            def _restore_opacity():
                try:
                    x11 = ctypes.CDLL('libX11.so.6')
                    disp = x11.XOpenDisplay(None)
                    if disp:
                        if sp_was_minimized:
                            x11.XIconifyWindow(disp, sp_win, 0)
                        atom_opacity = x11.XInternAtom(disp, b'_NET_WM_WINDOW_OPACITY', False)
                        opacity_full = ctypes.c_ulong(0xffffffff)
                        x11.XChangeProperty(disp, sp_win, atom_opacity, 6, 32, 0, ctypes.byref(opacity_full), 1)
                        x11.XFlush(disp)
                        x11.XCloseDisplay(disp)
                except Exception as e:
                    print(f"Error restoring Spotify opacity: {e}")
                return False

            GLib.timeout_add(20, _suppress)
            GLib.timeout_add(120, _suppress)
            GLib.timeout_add(300, _restore_opacity)

    def show_queue_loading(self, duration_ms=1500):
        if not hasattr(self, "queue_spinner") or not self.queue_spinner:
            return
        self._queue_loading_shown_at = time.time()
        self.queue_spinner.set_visible(True)
        self.queue_spinner.start()
        if getattr(self, "_hide_loading_timer", None):
            try:
                GLib.source_remove(self._hide_loading_timer)
            except Exception:
                pass
            self._hide_loading_timer = None

        def _hide():
            self.hide_queue_loading(force=True)
            return False

        # Safety fallback only; spinner is normally hidden explicitly when UI rebuild finishes
        self._hide_loading_timer = GLib.timeout_add(duration_ms, _hide)

    def hide_queue_loading(self, force=False):
        if not force and hasattr(self, "_queue_loading_shown_at"):
            elapsed = (time.time() - self._queue_loading_shown_at) * 1000
            min_duration = 350
            if elapsed < min_duration:
                remaining = int(min_duration - elapsed)
                GLib.timeout_add(remaining, lambda: self.hide_queue_loading(force=True) and False)
                return
        if getattr(self, "_hide_loading_timer", None):
            try:
                GLib.source_remove(self._hide_loading_timer)
            except Exception:
                pass
            self._hide_loading_timer = None
        if hasattr(self, "queue_spinner") and self.queue_spinner:
            self.queue_spinner.stop()
            self.queue_spinner.set_visible(False)

    def _rebuild_queue_ui(self, order_changed=False):
        """Debounced entry point: coalesces rapid back-to-back calls into one rebuild."""
        self._rebuild_order_changed = getattr(self, "_rebuild_order_changed", False) or order_changed

        if order_changed and getattr(self, "is_queue_open", False):
            self.show_queue_loading(duration_ms=1500)

        # Cancel any previously scheduled rebuild
        if getattr(self, "_rebuild_pending_id", None):
            try:
                GLib.source_remove(self._rebuild_pending_id)
            except Exception:
                pass
            self._rebuild_pending_id = None

        # Schedule actual rebuild after 60ms (coalesces rapid clicks so UI stays responsive)
        self._rebuild_pending_id = GLib.timeout_add(60, self._fire_rebuild)

    def _on_queue_track_clicked(self, uri):
        if not uri:
            return
        if getattr(self, "is_queue_open", False):
            self.show_queue_loading(duration_ms=1500)
        self.max_track_pos = 0.0
        self.anchor_pos = 0.0
        self.anchor_time = time.time()
        self.last_sync_pos = 0.0
        self._ignore_rewind_until = time.time() + 2.0
        idx = self.queue_mgr._find_track_idx(uri)
        if idx >= 0:
            self._skip_target_idx = idx
            self.queue_mgr.last_valid_idx = idx
        self.play_track_silent(uri)

    def _fire_rebuild(self):
        """Called by debounce timer — runs the actual rebuild with accumulated flags."""
        self._rebuild_pending_id = None
        oc = getattr(self, "_rebuild_order_changed", False)
        self._rebuild_order_changed = False
        self._do_rebuild_queue_ui(order_changed=oc)
        return False

    def _do_rebuild_queue_ui(self, order_changed=False):
        """Actual queue rebuild logic — always called via idle_add after spinner is shown."""
        vadj = self.queue_scroll.get_vadjustment() if hasattr(self, "queue_scroll") and self.queue_scroll else None
        saved_scroll_y = vadj.get_value() if vadj else 0.0

        # Clear existing children
        while True:
            child = self.queue_list_box.get_first_child()
            if not child:
                break
            self.queue_list_box.remove(child)

        self.curr_row = None
        is_loop = getattr(self.mpris, "loop_status", "Playlist") != "None"
        past_tracks = self.queue_mgr.get_past_tracks(limit=40, loop=is_loop)
        upcoming_tracks = self.queue_mgr.get_upcoming_tracks(limit=60, loop=is_loop)
        curr_track = self.queue_mgr.current_track or {}
        curr_uri = curr_track.get("uri") or self.mpris.track_id or ""
        last_uri = getattr(self, "_last_rebuilt_track_uri", None)
        track_changed = bool(curr_uri and last_uri and curr_uri != last_uri)
        self._last_rebuilt_track_uri = curr_uri

        # CRITICAL: Prioritize curr_track metadata so rapid skipping updates title immediately
        curr_title = curr_track.get("title")
        if not curr_title or curr_title in ("Трек", "Track"):
            tid = self.queue_mgr._extract_id(curr_uri)
            if tid and tid in self.queue_mgr.track_meta_cache:
                meta = self.queue_mgr.track_meta_cache[tid]
                if meta.get("title"):
                    curr_title = meta["title"]
        if not curr_title or curr_title in ("Трек", "Track"):
            curr_title = self.mpris.title or t("no_track")

        curr_artist = (curr_track.get("artist") or "").strip()
        if not curr_artist or curr_artist.casefold() == "spotify":
            tid = self.queue_mgr._extract_id(curr_uri)
            if tid and tid in self.queue_mgr.track_meta_cache:
                meta = self.queue_mgr.track_meta_cache[tid]
                if meta.get("artist") and meta["artist"].casefold() != "spotify":
                    curr_artist = meta["artist"]
        if not curr_artist or curr_artist.casefold() == "spotify":
            curr_artist = (self.mpris.artist or "").strip()
        if curr_artist.casefold() == "spotify":
            curr_artist = ""

        ctx_name = self.queue_mgr.get_context_name(current_album=self.mpris.album)
        if ctx_name and is_valid_name(ctx_name):
            clean = ctx_name.strip()
            display_name = clean[:33] + "..." if len(clean) > 36 else clean
            prefix = t("queue_prefix")
            header_text = f"{prefix}{display_name}"
            full_tooltip = f"{prefix}{clean}"
        else:
            header_text = t("queue_title")
            full_tooltip = t("queue_title")

        self.queue_header_label.set_text(header_text)
        self.queue_header_label.set_tooltip_text(full_tooltip)

        # 1. Past Tracks - At the TOP
        if past_tracks:
            sec_hist = Gtk.Label(label=t("recently_played"))
            sec_hist.set_xalign(0.0)
            sec_hist.add_css_class("queue-section-title")
            sec_hist.set_margin_top(4)
            self.queue_list_box.append(sec_hist)

            for idx, track in enumerate(past_tracks):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.add_css_class("queue-history-row")
                row.set_cursor_from_name("pointer")

                num_val = track.get("track_num", idx + 1)
                num_lbl = Gtk.Label(label=f"{num_val}")
                num_lbl.set_xalign(1.0)
                num_lbl.add_css_class("queue-num")
                row.append(num_lbl)

                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                info_box.set_hexpand(True)

                t_lbl = Gtk.Label(label=track.get("title", t("track_default")))
                t_lbl.set_xalign(0.0)
                t_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                t_lbl.add_css_class("queue-history-title")
                info_box.append(t_lbl)

                artist_val = (track.get("artist") or "").strip()
                if not artist_val or artist_val.casefold() == "spotify":
                    # Enrich from track_meta_cache (fetched artist data)
                    tid = (track.get("uri") or "").split(":")[-1]
                    if tid:
                        meta = self.queue_mgr.track_meta_cache.get(tid) or {}
                        artist_val = (meta.get("artist") or "").strip()
                        if artist_val.casefold() == "spotify":
                            artist_val = ""
                if artist_val:
                    a_lbl = Gtk.Label(label=artist_val)
                    a_lbl.set_xalign(0.0)
                    a_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    a_lbl.add_css_class("queue-history-artist")
                    info_box.append(a_lbl)

                row.append(info_box)

                h_icon = Gtk.Image.new_from_icon_name("media-playlist-repeat-symbolic")
                h_icon.add_css_class("queue-history-icon")
                h_icon.set_tooltip_text(t("play_again"))
                row.append(h_icon)

                uri = track.get("uri", "")
                click = Gtk.GestureClick()
                click.connect("released", lambda g, n, x, y, u=uri: self._on_queue_track_clicked(u))
                row.add_controller(click)

                self.queue_list_box.append(row)

        # 2. Currently Playing Track - IN THE CENTER
        sec_curr = Gtk.Label(label=t("now_playing"))
        sec_curr.set_xalign(0.0)
        sec_curr.add_css_class("queue-section-title")
        sec_curr.set_margin_top(6)
        self.queue_list_box.append(sec_curr)

        self.curr_row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
        self.curr_row.add_css_class("queue-current-row")

        curr_num = curr_track.get("track_num")
        if not curr_num and self._skip_target_idx is not None:
            curr_num = self._skip_target_idx + 1
        num_lbl = Gtk.Label(label=f"{curr_num}" if curr_num else "")
        num_lbl.set_xalign(1.0)
        num_lbl.add_css_class("queue-num")
        num_lbl.add_css_class("queue-current-num")
        self.curr_row.append(num_lbl)

        info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        info_box.set_hexpand(True)

        t_lbl = Gtk.Label(label=curr_title)
        t_lbl.set_xalign(0.0)
        t_lbl.set_ellipsize(Pango.EllipsizeMode.END)
        t_lbl.add_css_class("queue-current-title")
        info_box.append(t_lbl)

        if curr_artist:
            a_lbl = Gtk.Label(label=curr_artist)
            a_lbl.set_xalign(0.0)
            a_lbl.set_ellipsize(Pango.EllipsizeMode.END)
            a_lbl.add_css_class("queue-current-artist")
            info_box.append(a_lbl)

        self.curr_row.append(info_box)

        status_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        eq_icon = Gtk.Image.new_from_icon_name("audio-speakers-symbolic")
        eq_icon.add_css_class("queue-current-icon")
        status_box.append(eq_icon)

        badge = Gtk.Label(label=t("playing"))
        badge.add_css_class("queue-playing-badge")
        status_box.append(badge)

        self.curr_row.append(status_box)

        self.queue_list_box.append(self.curr_row)

        # 3. Upcoming Tracks Section - BELOW
        sec_next = Gtk.Label(label=t("next_in_queue"))
        sec_next.set_xalign(0.0)
        sec_next.add_css_class("queue-section-title")
        sec_next.set_margin_top(6)
        self.queue_list_box.append(sec_next)

        if not upcoming_tracks:
            empty_lbl = Gtk.Label(label=t("queue_empty"))
            empty_lbl.add_css_class("queue-item-artist")
            empty_lbl.set_margin_top(4)
            empty_lbl.set_margin_bottom(4)
            self.queue_list_box.append(empty_lbl)
        else:
            for idx, track in enumerate(upcoming_tracks):
                row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
                row.add_css_class("queue-row")
                row.set_cursor_from_name("pointer")

                num_val = track.get("track_num", idx + 1)
                num_lbl = Gtk.Label(label=f"{num_val}")
                num_lbl.set_xalign(1.0)
                num_lbl.add_css_class("queue-num")
                row.append(num_lbl)

                info_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
                info_box.set_hexpand(True)

                t_lbl = Gtk.Label(label=track.get("title", t("track_default")))
                t_lbl.set_xalign(0.0)
                t_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                t_lbl.add_css_class("queue-item-title")
                info_box.append(t_lbl)

                artist_val = (track.get("artist") or "").strip()
                if not artist_val or artist_val.casefold() == "spotify":
                    # Enrich from track_meta_cache (fetched artist data)
                    tid = (track.get("uri") or "").split(":")[-1]
                    if tid:
                        meta = self.queue_mgr.track_meta_cache.get(tid) or {}
                        artist_val = (meta.get("artist") or "").strip()
                        if artist_val.casefold() == "spotify":
                            artist_val = ""
                if artist_val:
                    a_lbl = Gtk.Label(label=artist_val)
                    a_lbl.set_xalign(0.0)
                    a_lbl.set_ellipsize(Pango.EllipsizeMode.END)
                    a_lbl.add_css_class("queue-item-artist")
                    info_box.append(a_lbl)

                row.append(info_box)

                play_hint = Gtk.Image.new_from_icon_name("media-playback-start-symbolic")
                play_hint.add_css_class("queue-play-hint")
                play_hint.set_tooltip_text(t("play_pause"))
                row.append(play_hint)

                uri = track.get("uri", "")
                click = Gtk.GestureClick()
                click.connect("released", lambda g, n, x, y, u=uri: self._on_queue_track_clicked(u))
                row.add_controller(click)

                self.queue_list_box.append(row)

        # Center scroll on current track if appropriate, otherwise preserve user's scroll position
        # NEVER disturb user scroll position if they are hovering or scrolled the queue
        user_interacting = getattr(self, "is_queue_hovered", False) or getattr(self, "_user_scrolled_queue", False)
        should_center = (
            not user_interacting and (
                order_changed or 
                track_changed or 
                not getattr(self, "_user_scrolled_queue", False)
            )
        )

        if should_center:
            self._user_scrolled_queue = False
            GLib.idle_add(self._scroll_to_current_track)
            GLib.timeout_add(50, self._scroll_to_current_track)
            GLib.timeout_add(150, self._scroll_to_current_track)
        else:
            if vadj and saved_scroll_y > 0:
                def _restore_scroll():
                    self._ignore_scroll_event = True
                    if vadj:
                        vadj.set_value(saved_scroll_y)
                    self._ignore_scroll_event = False
                    return False
                GLib.idle_add(_restore_scroll)
                GLib.timeout_add(50, _restore_scroll)

        # Cleanly hide spinner when queue list rebuild finishes
        def _show_list_and_hide_spinner():
            self.hide_queue_loading()
            return False
        GLib.idle_add(_show_list_and_hide_spinner)

    def _on_poll_window_state(self):
        if getattr(self, "_switching_track", False):
            return True

        sp_on_screen = is_spotify_on_screen() or is_spotify_active()

        if sp_on_screen:
            # If Spotify desktop is open on screen, hide the mini player (unless user is actively scrubbing or hovering it)
            if self.get_visible() and not self.is_hovered and not self.is_scrubbing:
                self._cancel_hide_timer()
                self._cancel_fade()
                self._was_pinned_before_spotify = self.is_pinned
                self._hidden_due_to_spotify = True
                self.set_visible(False)
        else:
            # Spotify desktop is minimized or closed
            if getattr(self, "_hidden_due_to_spotify", False):
                self._hidden_due_to_spotify = False
                was_pinned = getattr(self, "_was_pinned_before_spotify", False)
                self._was_pinned_before_spotify = False
                # Reopen ONLY if the mini player was pinned! If not pinned, do not reopen.
                if self.is_pinned and was_pinned:
                    self.show_osd(force=True)
                    self._user_scrolled_queue = False
                    if self.is_queue_open:
                        GLib.idle_add(self._scroll_to_current_track)
                        GLib.timeout_add(60, self._scroll_to_current_track)
                        GLib.timeout_add(180, self._scroll_to_current_track)
                        GLib.timeout_add(320, self._scroll_to_current_track)

        # If mini player is minimized by window manager or system, unpin it
        surface = self.get_surface()
        if surface and isinstance(surface, Gdk.Toplevel):
            if surface.get_state() & Gdk.ToplevelState.MINIMIZED:
                if self.is_pinned:
                    self.set_pinned(False)
        if isinstance(surface, GdkX11.X11Surface):
            my_xid = surface.get_xid()
            if is_window_minimized(my_xid):
                if self.is_pinned:
                    self.set_pinned(False)

            # Check if pointer physically left the window to fix stuck is_hovered
            if self.get_visible() and not self.is_pinned:
                pointer_inside = is_pointer_over_window(my_xid, self.get_width(), self.get_height())
                if not pointer_inside:
                    if self.is_hovered:
                        self.is_hovered = False
                    if not self.hide_timer_id and not self.is_scrubbing and self.get_opacity() > 0.9:
                        self._schedule_hide(3500)

        # Track window position for saving when visible
        if self.get_visible():
            surface = self.get_surface()
            if isinstance(surface, GdkX11.X11Surface):
                xid = surface.get_xid()
                pos = get_window_pos(xid)
                if pos:
                    cur_x, cur_y = pos
                    if cur_x > 0 and cur_y > 0 and (cur_x != self.saved_x or cur_y != self.saved_y):
                        self.saved_x = cur_x
                        self.saved_y = cur_y
                        self._save_config()

        # Check for Spotify language changes every 1 second (5 * 200ms)
        self._lang_poll_counter = getattr(self, "_lang_poll_counter", 0) + 1
        if self._lang_poll_counter >= 5:
            self._lang_poll_counter = 0
            new_lang = i18n.detect_spotify_language()
            if new_lang != i18n.get_current_language():
                print(f"[i18n] Spotify language changed from {i18n.get_current_language()} to {new_lang}. Auto-restarting...")
                i18n.set_language(new_lang)
                self._restart_app()

        return True

    def _restart_app(self):
        try:
            self._save_config()
            self.set_visible(False)
            python = sys.executable
            script = os.path.join(os.path.dirname(os.path.abspath(__file__)), "main.py")
            os.execv(python, [python, script] + sys.argv[1:])
        except Exception as e:
            print(f"Failed to auto-restart: {e}")

    def _on_shuffle_clicked(self, button=None):
        self.user_shuffle = not getattr(self, "user_shuffle", False)
        self.mpris.set_shuffle(self.user_shuffle)
        self._save_config()
        if self.user_shuffle:
            curr_uri = self.mpris.track_id or (self.queue_mgr.current_track.get("uri") if self.queue_mgr.current_track else "")
            self.shuffle_history = [curr_uri] if curr_uri else []
        self._update_playback_options_ui()
        if getattr(self, "is_queue_open", False):
            self.show_queue_loading(duration_ms=1500)
        self._rebuild_queue_ui(order_changed=True)

    def _on_repeat_clicked(self, button=None):
        old_loop = getattr(self.mpris, "loop_status", "None")
        self.mpris.cycle_loop_status()
        new_loop = getattr(self.mpris, "loop_status", "None")
        self._last_loop_status = new_loop
        self._update_playback_options_ui()
        if old_loop != new_loop:
            if getattr(self, "is_queue_open", False):
                self.show_queue_loading(duration_ms=1500)
            self._rebuild_queue_ui(order_changed=True)
            self._scroll_to_current_track()

    def _on_mpris_options_changed(self, shuffle, loop_status):
        old_loop = getattr(self, "_last_loop_status", None)
        old_shuffle = getattr(self, "_last_shuffle_status", None)
        self._last_loop_status = loop_status

        if is_spotify_active():
            self.user_shuffle = shuffle
            self._save_config()
        else:
            if shuffle != getattr(self, "user_shuffle", False):
                self.mpris.set_shuffle(self.user_shuffle)
                shuffle = self.user_shuffle

        self._last_shuffle_status = shuffle
        GLib.idle_add(self._update_playback_options_ui)
        mode_changed = (old_loop is not None and old_loop != loop_status) or (old_shuffle is not None and old_shuffle != shuffle)
        if mode_changed:
            def _refresh():
                if getattr(self, "is_queue_open", False):
                    self.show_queue_loading(duration_ms=1500)
                self._rebuild_queue_ui(order_changed=True)
                self._scroll_to_current_track()
                return False
            GLib.idle_add(_refresh)

    def _update_playback_options_ui(self):
        if not hasattr(self, "shuffle_btn") or not hasattr(self, "repeat_btn"):
            return

        # 1. Shuffle state
        is_shuffled = getattr(self, "user_shuffle", False)
        if is_shuffled:
            self.shuffle_btn.add_css_class("active-control")
            self.shuffle_btn.set_tooltip_text(t("shuffle_enabled"))
        else:
            self.shuffle_btn.remove_css_class("active-control")
            self.shuffle_btn.set_tooltip_text(t("shuffle_disabled"))

        # 2. Repeat state (3 modes: None, Playlist, Track)
        loop = getattr(self.mpris, "loop_status", "None")
        if loop == "Track":
            self.repeat_btn.set_icon_name("media-playlist-repeat-song-symbolic")
            self.repeat_btn.add_css_class("active-control")
            self.repeat_btn.set_tooltip_text(t("repeat_track"))
        elif loop == "Playlist":
            self.repeat_btn.set_icon_name("media-playlist-repeat-symbolic")
            self.repeat_btn.add_css_class("active-control")
            self.repeat_btn.set_tooltip_text(t("repeat_playlist"))
        else:
            self.repeat_btn.set_icon_name("media-playlist-repeat-symbolic")
            self.repeat_btn.remove_css_class("active-control")
            self.repeat_btn.set_tooltip_text(t("repeat_none"))

    def _update_ui_strings(self):
        self.vol_btn.set_tooltip_text(t("mute_unmute"))
        self.vol_scale.set_tooltip_text(t("spotify_volume"))
        self.cover_box.set_tooltip_text(t("focus_spotify"))
        self.queue_btn.set_tooltip_text(t("queue_tooltip"))
        self.pin_btn.set_tooltip_text(t("pin_on") if self.is_pinned else t("pin_off"))
        self.raise_btn.set_tooltip_text(t("open_spotify"))
        if hasattr(self, "min_btn"):
            self.min_btn.set_tooltip_text(t("minimize_player"))
        self.close_btn.set_tooltip_text(t("close_player"))
        self.prev_btn.set_tooltip_text(t("prev_track"))
        self.play_btn.set_tooltip_text(t("play_pause"))
        self.next_btn.set_tooltip_text(t("next_track"))
        if hasattr(self, "q_refresh_btn"):
            self.q_refresh_btn.set_tooltip_text(t("refresh_queue"))
        if hasattr(self, "q_close_btn"):
            self.q_close_btn.set_tooltip_text(t("collapse_queue"))
        if hasattr(self, "off_title"):
            self.off_title.set_text(t("offline_title"))
        if hasattr(self, "launch_btn"):
            self.launch_btn.set_label(t("launch_spotify"))
        self._update_volume_icon(self.current_volume)
        self._update_playback_options_ui()
        self._rebuild_queue_ui()

    def _on_mouse_enter(self, controller, x, y):
        if not self.get_visible() or self.get_opacity() < 0.1:
            return
        self.is_hovered = True
        self._cancel_hide_timer()
        self._cancel_fade()
        self.set_opacity(1.0)

    def _on_mouse_leave(self, controller):
        self.is_hovered = False
        if not self.is_pinned and not self.is_scrubbing:
            self._schedule_hide(3500)

    def _on_scroll_volume(self, controller, dx, dy):
        if self.is_queue_open:
            return False
        return self._do_volume_scroll(dy)

    def _on_vol_box_scroll(self, controller, dx, dy):
        return self._do_volume_scroll(dy)

    def _do_volume_scroll(self, dy):
        delta = -0.04 if dy > 0 else 0.04
        new_vol = max(0.0, min(1.0, self.current_volume + delta))
        self.current_volume = new_vol
        self.vol_scale.set_value(new_vol * 100)
        self.mpris.set_volume(new_vol)
        self._update_volume_icon(new_vol)
        self.show_osd(2500)
        return True

    def _on_scale_change_value(self, scale, scroll, value):
        self.is_scrubbing = True
        self.anchor_pos = value
        self.anchor_time = time.time()
        self.scale.set_value(value)
        self.pos_label.set_text(format_time(value))

        if self.seek_timer_id:
            GLib.source_remove(self.seek_timer_id)

        def do_seek():
            self.mpris.set_position(value * 1_000_000)
            self.seek_timer_id = None
            GLib.timeout_add(150, self._clear_scrubbing)
            return False

        self.seek_timer_id = GLib.timeout_add(50, do_seek)
        return False

    def _clear_scrubbing(self):
        self.is_scrubbing = False
        if not self.is_pinned and not self.is_hovered:
            self._schedule_hide(3500)
        return False

    def _is_song_at_end(self):
        dur = getattr(self, "duration_sec", 0.0)
        if dur <= 3.0:
            return False
        max_pos = getattr(self, "max_track_pos", 0.0)
        last_pos = getattr(self, "last_sync_pos", 0.0)
        cur_pos = max(max_pos, last_pos)
        return (cur_pos >= max(1.0, dur - 6.0)) or (cur_pos >= dur * 0.85 and dur > 15.0)

    def _trigger_auto_next_track(self):
        if getattr(self, "_switching_track", False):
            return False
        if getattr(self, "is_scrubbing", False):
            return False
        if time.time() < getattr(self, "_ignore_rewind_until", 0.0):
            return False

        loop = getattr(self.mpris, "loop_status", "None")
        if loop == "Track":
            return False

        all_tracks = self.queue_mgr.all_context_tracks
        if not all_tracks or len(all_tracks) <= 1:
            return False

        self._ignore_rewind_until = time.time() + 2.5
        self.max_track_pos = 0.0
        self.last_sync_pos = 0.0
        self.anchor_pos = 0.0
        self.anchor_time = time.time()

        self.on_user_next_clicked()
        return True

    def _on_tick(self):
        if not self.mpris.is_available:
            return True

        if self.mpris.playback_status == "Playing" and not self.is_scrubbing:
            elapsed = time.time() - self.anchor_time
            curr = min(self.duration_sec, self.anchor_pos + elapsed)
            self.scale.set_value(curr)
            self.pos_label.set_text(format_time(curr))
            if curr > getattr(self, "max_track_pos", 0.0):
                self.max_track_pos = curr

            # Real-time overrun check (song finished playing)
            if self.duration_sec > 3.0 and (self.anchor_pos + elapsed) >= self.duration_sec + 0.3:
                if not getattr(self, "_switching_track", False) and time.time() >= getattr(self, "_ignore_rewind_until", 0.0):
                    fresh_us = self.mpris.get_fresh_position()
                    if fresh_us >= 0:
                        fresh_sec = fresh_us / 1_000_000.0
                        if fresh_sec < 4.5 or fresh_sec >= max(1.0, self.duration_sec - 1.2):
                            if self._is_song_at_end():
                                self._trigger_auto_next_track()

        return True

    def _on_fast_sync(self):
        if not self.mpris.is_available or self.is_scrubbing or getattr(self, "_switching_track", False):
            return True

        fresh_us = self.mpris.get_fresh_position()
        if fresh_us >= 0:
            fresh_sec = fresh_us / 1_000_000.0
            if fresh_sec > getattr(self, "max_track_pos", 0.0):
                self.max_track_pos = fresh_sec

            is_rewound_to_start = (
                self._is_song_at_end() and
                (fresh_sec < 4.5 or fresh_sec < self.last_sync_pos - 15.0)
            )

            is_rewind = is_rewound_to_start or (self.last_sync_pos > fresh_sec + 0.5 and fresh_sec < 2.0)
            if is_rewind:
                self.anchor_pos = fresh_sec
                self.anchor_time = time.time()
                self.scale.set_value(fresh_sec)
                self.pos_label.set_text(format_time(fresh_sec))

                if is_rewound_to_start and time.time() >= getattr(self, "_ignore_rewind_until", 0.0):
                    if self._trigger_auto_next_track():
                        self.last_sync_pos = fresh_sec
                        return True
            elif abs(fresh_sec - (self.anchor_pos + (time.time() - self.anchor_time))) > 1.2:
                self.anchor_pos = fresh_sec
                self.anchor_time = time.time()

            self.last_sync_pos = fresh_sec
        return True

    def _on_mpris_seeked(self, pos_us):
        if not self.mpris.is_available or self.is_scrubbing:
            return
        fresh_sec = pos_us / 1_000_000.0
        if self._is_song_at_end() and fresh_sec < 4.5 and time.time() >= getattr(self, "_ignore_rewind_until", 0.0):
            if self._trigger_auto_next_track():
                return

        self.anchor_pos = fresh_sec
        self.anchor_time = time.time()
        self.last_sync_pos = fresh_sec
        self.scale.set_value(fresh_sec)
        self.pos_label.set_text(format_time(fresh_sec))

    def _on_user_media_action(self, key=""):
        key_str = str(key).strip().lower()
        if key_str in ("next", "nexttrack"):
            if self.queue_mgr.all_context_tracks:
                self.on_user_next_clicked()
            else:
                self.mpris.next()
        elif key_str in ("previous", "prev", "prevtrack"):
            if self.queue_mgr.all_context_tracks:
                self.on_user_prev_clicked()
            else:
                fresh_us = self.mpris.get_fresh_position()
                if fresh_us > 3_000_000:
                    self._ignore_rewind_until = time.time() + 1.5
                self.mpris.previous()
        elif key_str in ("play", "pause", "playpause"):
            self.mpris.play_pause()

        fresh_us = self.mpris.get_fresh_position()
        if fresh_us >= 0:
            fresh_sec = fresh_us / 1_000_000.0
            self.anchor_pos = fresh_sec
            self.anchor_time = time.time()
            self.scale.set_value(fresh_sec)
            self.pos_label.set_text(format_time(fresh_sec))

        self.show_osd(4500, force=True)

    def show_osd(self, duration_ms=4500, force=False):
        # If user is working actively in Spotify, do NOT pop up unless forced
        if not force and is_spotify_active():
            return

        self._cancel_fade()
        self.set_opacity(1.0)
        self.set_visible(True)
        surface = self.get_surface()
        if surface and isinstance(surface, Gdk.Toplevel) and (surface.get_state() & Gdk.ToplevelState.MINIMIZED):
            self.unminimize()
        elif isinstance(surface, GdkX11.X11Surface) and is_window_minimized(surface.get_xid()):
            self.unminimize()
        self.present()

        surface = self.get_surface()
        if isinstance(surface, GdkX11.X11Surface):
            xid = surface.get_xid()
            set_window_motif_hints(xid)
            set_window_always_above(xid, True)
            if self.saved_x is not None and self.saved_y is not None:
                move_window_to(xid, self.saved_x, self.saved_y)
            if not is_pointer_over_window(xid, self.get_width(), self.get_height()):
                self.is_hovered = False

        if not self.is_pinned:
            if not self.is_hovered and not self.is_scrubbing:
                self._schedule_hide(duration_ms)

        if self.is_queue_open:
            self._user_scrolled_queue = False
            GLib.idle_add(self._scroll_to_current_track)
            GLib.timeout_add(60, self._scroll_to_current_track)
            GLib.timeout_add(180, self._scroll_to_current_track)
            GLib.timeout_add(320, self._scroll_to_current_track)

    def _schedule_hide(self, duration_ms):
        self._cancel_hide_timer()
        self.hide_timer_id = GLib.timeout_add(duration_ms, self._on_autohide_timer)

    def _cancel_hide_timer(self):
        if self.hide_timer_id:
            GLib.source_remove(self.hide_timer_id)
            self.hide_timer_id = None

    def _cancel_fade(self):
        if self.fade_timer_id:
            GLib.source_remove(self.fade_timer_id)
            self.fade_timer_id = None

    def _on_autohide_timer(self):
        self.hide_timer_id = None
        if self.is_pinned or self.is_hovered or self.is_scrubbing:
            return False

        self._start_fade_out()
        return False

    def _start_fade_out(self):
        self._cancel_fade()
        step = 0
        total_steps = 22

        def fade_step():
            nonlocal step
            if self.is_pinned or self.is_hovered or self.is_scrubbing:
                self.set_opacity(1.0)
                self.fade_timer_id = None
                return False

            step += 1
            t = step / float(total_steps)
            if t >= 1.0:
                self.set_opacity(0.0)
                self.set_visible(False)
                self.fade_timer_id = None
                self.is_hovered = False
                self.is_scrubbing = False
                return False

            ease = t * t * (3.0 - 2.0 * t)
            opacity = max(0.0, 1.0 - ease)
            self.set_opacity(opacity)
            return True

        self.fade_timer_id = GLib.timeout_add(16, fade_step)

    def hide_osd_immediate(self):
        self._cancel_hide_timer()
        self._cancel_fade()
        self.is_hovered = False
        self.is_scrubbing = False
        self.set_visible(False)

    def _on_key_pressed(self, controller, keyval, keycode, state):
        if (state & Gdk.ModifierType.CONTROL_MASK) and (keyval in (Gdk.KEY_q, Gdk.KEY_Q)):
            self._on_quit_app()
            return True
        return False

    def _on_close_request(self, window):
        self._on_quit_app()
        return False

    def _on_minimize_clicked(self, button=None):
        if self.is_pinned:
            self.set_pinned(False)
        self.hide_osd_immediate()

    def _on_close_clicked(self, button=None):
        self._on_quit_app()

    def _on_quit_app(self):
        if hasattr(self, "queue_mgr") and self.queue_mgr:
            self.queue_mgr.save_cache()
        app = self.get_application()
        if app:
            app.quit()
        else:
            self.close()
            sys.exit(0)

    def set_pinned(self, pinned: bool):
        self.is_pinned = bool(pinned)
        if not self.is_pinned:
            self._was_pinned_before_spotify = False
        self._save_config()
        self._cancel_hide_timer()
        self._cancel_fade()
        self.set_opacity(1.0)

        surface = self.get_surface()
        if isinstance(surface, GdkX11.X11Surface):
            xid = surface.get_xid()
            set_window_always_above(xid, self.is_pinned)

        if self.is_pinned:
            self.pin_btn.add_css_class("pinned-active")
            self.pin_btn.set_tooltip_text(t("pin_on"))
        else:
            self.pin_btn.remove_css_class("pinned-active")
            self.pin_btn.set_tooltip_text(t("pin_off"))

    def _toggle_pin(self, button=None):
        new_state = not self.is_pinned
        self.set_pinned(new_state)
        if not new_state:
            self._schedule_hide(3500)

    def _launch_spotify(self, button):
        try:
            subprocess.Popen(["spotify"])
        except Exception as e:
            print(f"Error launching spotify: {e}")

    def _on_availability_changed(self, available):
        GLib.idle_add(self._update_availability_ui, available)

    def _update_availability_ui(self, available):
        if available:
            self.stack.set_visible_child_name("player")
            self._apply_metadata(track_changed=True)
            if self.is_pinned or self.get_visible():
                self.show_osd(4500)
        else:
            self.stack.set_visible_child_name("offline")
            self.visualizer.set_playing(False)
        return False

    def _on_metadata_updated(self, track_changed=False, is_user_action=False):
        GLib.idle_add(lambda: self._apply_metadata(track_changed=track_changed, is_user_action=is_user_action))

    def _apply_metadata(self, track_changed=False, is_user_action=False):
        if not self.mpris.is_available:
            self.stack.set_visible_child_name("offline")
            return False

        if getattr(self, "_skip_target_idx", None) is not None or (getattr(self, "_switching_track", False) and getattr(self, "_pending_target_uri", None)):
            incoming_tid = self.queue_mgr._extract_id(self.mpris.track_id)
            target_tid = self.queue_mgr._extract_id(self._pending_target_uri) if getattr(self, "_pending_target_uri", None) else None
            target_title = None
            if getattr(self, "_skip_target_idx", None) is not None:
                all_tracks = self.queue_mgr.all_context_tracks
                if 0 <= self._skip_target_idx < len(all_tracks):
                    t_item = all_tracks[self._skip_target_idx]
                    if not target_tid:
                        target_tid = self.queue_mgr._extract_id(t_item.get("uri", ""))
                    target_title = t_item.get("title", "")

            # If user is still rapidly clicking (debounce timer active), wait for debounce:
            if getattr(self, "_skip_debounce_id", None) is not None:
                return False

            matches_target = False
            if target_tid and incoming_tid == target_tid:
                matches_target = True
            elif target_title and self.mpris.title and target_title.strip().lower() == self.mpris.title.strip().lower():
                matches_target = True

            if matches_target or not getattr(self, "_switching_track", False):
                self._pending_target_uri = None
                self._switching_track = False
                self._skip_target_idx = None
                if getattr(self, "_switching_timer_id", None):
                    try:
                        GLib.source_remove(self._switching_timer_id)
                    except Exception:
                        pass
                    self._switching_timer_id = None
            elif target_tid and not matches_target:
                return False

        if getattr(self, "_switching_track", False) and not getattr(self, "_pending_target_uri", None) and getattr(self, "_skip_target_idx", None) is None:
            self._switching_track = False
            if getattr(self, "_switching_timer_id", None):
                try:
                    GLib.source_remove(self._switching_timer_id)
                except Exception:
                    pass
                self._switching_timer_id = None

        self.stack.set_visible_child_name("player")
        self.title_label.set_text(self.mpris.title)
        self.title_label.set_tooltip_text(self.mpris.title)

        artist_text = format_artist_and_album(self.mpris.artist, self.mpris.album, self.mpris.title)
        self.artist_label.set_text(artist_text)
        self.artist_label.set_tooltip_text(artist_text)

        new_duration = self.mpris.length_us / 1_000_000.0
        self.duration_sec = new_duration
        self.scale.set_range(0, max(1, self.duration_sec))
        self.dur_label.set_text(format_time(self.duration_sec))

        if track_changed or self.mpris.track_id != self.last_track_id:
            if getattr(self, "is_queue_open", False):
                self.show_queue_loading(duration_ms=1500)
            self._user_scrolled_queue = False
            self.max_track_pos = 0.0
            self.curr_track_duration = new_duration
            self.last_track_id = self.mpris.track_id
            self.anchor_pos = 0.0
            self.anchor_time = time.time()
            self.last_sync_pos = 0.0
            self.scale.set_value(0)
            self.pos_label.set_text("00:00")

            if self.mpris.track_id:
                if not hasattr(self, "shuffle_history") or self.shuffle_history is None:
                    self.shuffle_history = []
                if not self.shuffle_history or self.shuffle_history[-1] != self.mpris.track_id:
                    self.shuffle_history.append(self.mpris.track_id)
                    if len(self.shuffle_history) > 100:
                        self.shuffle_history.pop(0)

            # Update current track in queue (notify inside will trigger debounced _rebuild_queue_ui)
            self.queue_mgr.update_current_track(self.mpris.title, self.mpris.artist, self.mpris.track_id, album=self.mpris.album)
        else:
            fresh_us = self.mpris.get_fresh_position()
            self.anchor_pos = fresh_us / 1_000_000.0
            self.anchor_time = time.time()
            self.last_sync_pos = self.anchor_pos
            self.scale.set_value(self.anchor_pos)
            self.pos_label.set_text(format_time(self.anchor_pos))

        if self.mpris.local_art_path and os.path.exists(self.mpris.local_art_path):
            try:
                pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(self.mpris.local_art_path, 84, 84, True)
                paintable = Gdk.Texture.new_for_pixbuf(pixbuf)
                self.cover_pic.set_paintable(paintable)
            except Exception:
                self.cover_pic.set_filename(self.mpris.local_art_path)
        else:
            self.cover_pic.set_paintable(None)

        self._apply_status(self.mpris.playback_status)

        if is_user_action:
            self.show_osd(4500)

        return False

    def _on_status_updated(self, status, is_user_action=False):
        GLib.idle_add(lambda: self._apply_status_and_osd(status, is_user_action))

    def _apply_status_and_osd(self, status, is_user_action):
        self._apply_status(status)
        if status == "Playing":
            fresh_us = self.mpris.get_fresh_position()
            self.anchor_pos = fresh_us / 1_000_000.0
            self.anchor_time = time.time()
            self.last_sync_pos = self.anchor_pos
            if self.anchor_pos > getattr(self, "max_track_pos", 0.0):
                self.max_track_pos = self.anchor_pos
        elif status in ("Paused", "Stopped"):
            self.anchor_pos = self.scale.get_value()
            if self._is_song_at_end() and not getattr(self, "is_scrubbing", False) and time.time() >= getattr(self, "_ignore_rewind_until", 0.0):
                if self._trigger_auto_next_track():
                    return False

        if is_user_action:
            self.show_osd(4500)
        return False

    def _apply_status(self, status):
        is_playing = (status == "Playing")

        if is_playing:
            self.play_btn.set_icon_name("media-playback-pause-symbolic")
            self.play_btn.set_tooltip_text(t("pause"))
            self.play_btn.add_css_class("playing")
            self.visualizer.start()
        else:
            self.play_btn.set_icon_name("media-playback-start-symbolic")
            self.play_btn.set_tooltip_text(t("play"))
            self.play_btn.remove_css_class("playing")
            self.visualizer.stop()

        return False

class SpotifyMiniApp(Adw.Application):
    def __init__(self):
        super().__init__(
            application_id="com.github.vibe.spotifymini",
            flags=Gio.ApplicationFlags.HANDLES_COMMAND_LINE
        )
        self.win = None
        self.add_main_option("next", ord("n"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Skip to next track", None)
        self.add_main_option("prev", ord("p"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Skip to previous track", None)
        self.add_main_option("play-pause", ord("t"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Toggle play/pause", None)
        self.add_main_option("show", ord("s"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Show mini player HUD", None)
        self.add_main_option("hide", ord("h"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Hide mini player HUD", None)
        self.add_main_option("toggle-visible", ord("v"), GLib.OptionFlags.NONE, GLib.OptionArg.NONE, "Toggle window visibility", None)

    def do_command_line(self, command_line):
        options = command_line.get_options_dict()
        self.activate()
        if options.contains("next"):
            GLib.idle_add(self.win.on_user_next_clicked)
            GLib.idle_add(lambda: self.win.show_osd(4500, force=True))
        elif options.contains("prev"):
            self.win._ignore_rewind_until = time.time() + 1.5
            GLib.idle_add(self.win.on_user_prev_clicked)
            GLib.idle_add(lambda: self.win.show_osd(4500, force=True))
        elif options.contains("play-pause"):
            GLib.idle_add(self.win.mpris.play_pause)
            GLib.idle_add(lambda: self.win.show_osd(4500, force=True))
        elif options.contains("show"):
            GLib.idle_add(lambda: self.win.show_osd(4500, force=True))
        elif options.contains("hide"):
            GLib.idle_add(self.win.hide_osd_immediate)
        elif options.contains("toggle-visible"):
            GLib.idle_add(lambda: self.win.hide_osd_immediate() if (self.win.get_visible() and self.win.get_opacity() > 0.1) else self.win.show_osd(4500, force=True))
        else:
            GLib.idle_add(lambda: self.win.show_osd(4500, force=True))
        command_line.set_exit_status(0)
        command_line.done()
        return 0

    def do_activate(self):
        style_mgr = Adw.StyleManager.get_default()
        style_mgr.set_color_scheme(Adw.ColorScheme.FORCE_DARK)

        css_path = os.path.join(os.path.dirname(os.path.abspath(__file__)), "style.css")
        if os.path.exists(css_path):
            provider = Gtk.CssProvider()
            provider.load_from_path(css_path)
            Gtk.StyleContext.add_provider_for_display(
                Gdk.Display.get_default(),
                provider,
                Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION
            )

        if not self.win:
            self.hold()
            self.win = SpotifyMiniWindow(application=self)
        else:
            self.win.show_osd(4500)

if __name__ == "__main__":
    app = SpotifyMiniApp()
    app.run(sys.argv)
