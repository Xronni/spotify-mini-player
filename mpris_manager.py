import os
import urllib.request
import hashlib
from gi.repository import Gio, GLib

SPOTIFY_BUS_NAME = "org.mpris.MediaPlayer2.spotify"
MPRIS_OBJECT_PATH = "/org/mpris/MediaPlayer2"
PLAYER_INTERFACE = "org.mpris.MediaPlayer2.Player"
ROOT_INTERFACE = "org.mpris.MediaPlayer2"
PROPS_INTERFACE = "org.freedesktop.DBus.Properties"
CACHE_DIR = os.path.expanduser("~/.cache/spotify-mini-player/covers")

class MPRISManager:
    def __init__(self, on_update_cb=None, on_status_cb=None, on_avail_cb=None, on_media_key_cb=None, on_volume_cb=None):
        self.on_update_cb = on_update_cb
        self.on_status_cb = on_status_cb
        self.on_avail_cb = on_avail_cb
        self.on_media_key_cb = on_media_key_cb
        self.on_volume_cb = on_volume_cb

        self.bus = Gio.bus_get_sync(Gio.BusType.SESSION, None)
        self.player_proxy = None
        self.root_proxy = None
        self.props_proxy = None
        self.is_available = False

        self.title = "No Track Playing"
        self.artist = "Spotify"
        self.album = ""
        self.art_url = ""
        self.local_art_path = None
        self.playback_status = "Stopped"
        self.length_us = 0
        self.track_id = ""

        os.makedirs(CACHE_DIR, exist_ok=True)

        self._subscribe_signals()
        self.connect_spotify()

    def _subscribe_signals(self):
        # 1. Watch for Spotify process starting or closing
        self.bus.signal_subscribe(
            "org.freedesktop.DBus",
            "org.freedesktop.DBus",
            "NameOwnerChanged",
            "/org/freedesktop/DBus",
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_name_owner_changed,
            None
        )

        # 2. Watch for property changes (track change, status, volume)
        self.bus.signal_subscribe(
            SPOTIFY_BUS_NAME,
            PROPS_INTERFACE,
            "PropertiesChanged",
            MPRIS_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_properties_changed,
            None
        )

        # 3. Watch for Seeked signal
        self.bus.signal_subscribe(
            SPOTIFY_BUS_NAME,
            PLAYER_INTERFACE,
            "Seeked",
            MPRIS_OBJECT_PATH,
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_seeked,
            None
        )

        # 4. Watch for System Media Keys from GNOME SettingsDaemon
        self.bus.signal_subscribe(
            "org.gnome.SettingsDaemon.MediaKeys",
            "org.gnome.SettingsDaemon.MediaKeys",
            "MediaPlayerKeyPressed",
            "/org/gnome/SettingsDaemon/MediaKeys",
            None,
            Gio.DBusSignalFlags.NONE,
            self._on_media_key_pressed,
            None
        )

    def connect_spotify(self):
        try:
            self.player_proxy = Gio.DBusProxy.new_sync(
                self.bus,
                Gio.DBusProxyFlags.NONE,
                None,
                SPOTIFY_BUS_NAME,
                MPRIS_OBJECT_PATH,
                PLAYER_INTERFACE,
                None
            )
            self.root_proxy = Gio.DBusProxy.new_sync(
                self.bus,
                Gio.DBusProxyFlags.NONE,
                None,
                SPOTIFY_BUS_NAME,
                MPRIS_OBJECT_PATH,
                ROOT_INTERFACE,
                None
            )
            self.props_proxy = Gio.DBusProxy.new_sync(
                self.bus,
                Gio.DBusProxyFlags.NONE,
                None,
                SPOTIFY_BUS_NAME,
                MPRIS_OBJECT_PATH,
                PROPS_INTERFACE,
                None
            )

            owner = self.player_proxy.get_name_owner()
            if owner:
                self.is_available = True
                self.refresh_metadata()
            else:
                self.is_available = False

            if self.on_avail_cb:
                self.on_avail_cb(self.is_available)
        except Exception as e:
            print(f"Error connecting to Spotify MPRIS: {e}")
            self.is_available = False
            if self.on_avail_cb:
                self.on_avail_cb(False)

    def _on_name_owner_changed(self, connection, sender, path, iface, signal, params, user_data):
        name, old_owner, new_owner = params.unpack()
        if name == SPOTIFY_BUS_NAME:
            if new_owner:
                self.is_available = True
                self.connect_spotify()
            else:
                self.is_available = False
                self.playback_status = "Stopped"
                if self.on_avail_cb:
                    self.on_avail_cb(False)

    def _on_media_key_pressed(self, conn, sender, path, iface, signal, params, user_data):
        if self.on_media_key_cb:
            GLib.idle_add(self.on_media_key_cb)

    def _on_seeked(self, conn, sender, path, iface, signal, params, user_data):
        if self.on_media_key_cb:
            GLib.idle_add(self.on_media_key_cb)

    def _on_properties_changed(self, connection, sender, path, iface, signal, params, user_data):
        interface_name, changed, invalidated = params.unpack()
        if interface_name == PLAYER_INTERFACE:
            track_changed = False
            if "Metadata" in changed:
                track_changed = self._parse_metadata(changed["Metadata"])
            if "PlaybackStatus" in changed:
                self.playback_status = changed["PlaybackStatus"]
                if self.on_status_cb:
                    self.on_status_cb(self.playback_status, is_user_action=True)
            if "Volume" in changed:
                vol = changed["Volume"]
                if self.on_volume_cb:
                    self.on_volume_cb(vol)
            if self.on_update_cb:
                self.on_update_cb(track_changed=track_changed, is_user_action=True)

    def refresh_metadata(self):
        if not self.player_proxy:
            return
        try:
            status_prop = self.player_proxy.get_cached_property("PlaybackStatus")
            if status_prop:
                self.playback_status = status_prop.unpack()

            meta_prop = self.player_proxy.get_cached_property("Metadata")
            if meta_prop:
                self._parse_metadata(meta_prop.unpack())

            if self.on_update_cb:
                self.on_update_cb(track_changed=False, is_user_action=False)
        except Exception as e:
            print(f"Error refreshing metadata: {e}")

    def _parse_metadata(self, meta):
        old_track_id = self.track_id
        self.track_id = meta.get("mpris:trackid", "")
        track_changed = (self.track_id != old_track_id)

        self.title = meta.get("xesam:title", "Unknown Title")
        artists = meta.get("xesam:artist", ["Unknown Artist"])
        if isinstance(artists, list):
            self.artist = ", ".join(artists)
        else:
            self.artist = str(artists)
        self.album = meta.get("xesam:album", "")
        self.length_us = meta.get("mpris:length", 0)

        new_art_url = meta.get("mpris:artUrl", "")
        if new_art_url != self.art_url or track_changed:
            self.art_url = new_art_url
            self._download_cover_async(new_art_url)

        return track_changed

    def _download_cover_async(self, url):
        if not url:
            self.local_art_path = None
            if self.on_update_cb:
                self.on_update_cb(track_changed=False, is_user_action=False)
            return

        if url.startswith("file://"):
            self.local_art_path = url[7:]
            if self.on_update_cb:
                self.on_update_cb(track_changed=False, is_user_action=False)
            return

        url_hash = hashlib.md5(url.encode("utf-8")).hexdigest()
        ext = ".jpg" if ".jpg" in url or "scdn" in url else ".png"
        target_path = os.path.join(CACHE_DIR, f"{url_hash}{ext}")

        if os.path.exists(target_path):
            self.local_art_path = target_path
            if self.on_update_cb:
                self.on_update_cb(track_changed=False, is_user_action=False)
            return

        def worker():
            try:
                urllib.request.urlretrieve(url, target_path)
                self.local_art_path = target_path
                GLib.idle_add(lambda: self.on_update_cb(track_changed=False, is_user_action=False) if self.on_update_cb else False)
            except Exception as e:
                print(f"Failed to download cover art: {e}")

        import threading
        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def get_fresh_position(self):
        """Fetches uncached live Position directly from D-Bus in microseconds."""
        if not self.props_proxy or not self.is_available:
            return 0
        try:
            val = self.props_proxy.call_sync(
                "Get",
                GLib.Variant("(ss)", (PLAYER_INTERFACE, "Position")),
                Gio.DBusCallFlags.NONE,
                -1,
                None
            ).unpack()[0]
            return val
        except Exception:
            return 0

    def play_pause(self):
        if self.player_proxy:
            try:
                self.player_proxy.call_sync("PlayPause", None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                print(f"PlayPause failed: {e}")

    def next(self):
        if self.player_proxy:
            try:
                self.player_proxy.call_sync("Next", None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                print(f"Next failed: {e}")

    def previous(self):
        if self.player_proxy:
            try:
                self.player_proxy.call_sync("Previous", None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                print(f"Previous failed: {e}")

    def set_position(self, pos_us):
        if self.player_proxy and self.track_id:
            try:
                params = GLib.Variant("(ox)", (self.track_id, int(pos_us)))
                self.player_proxy.call_sync("SetPosition", params, Gio.DBusCallFlags.NONE, -1, None)
            except Exception:
                try:
                    curr = self.get_fresh_position()
                    offset = int(pos_us - curr)
                    self.player_proxy.call_sync("Seek", GLib.Variant("(x)", (offset,)), Gio.DBusCallFlags.NONE, -1, None)
                except Exception as e:
                    print(f"Seeking failed: {e}")

    def open_uri(self, uri):
        """Tells Spotify to immediately play the specified track URI."""
        if self.player_proxy and uri:
            try:
                self.player_proxy.call_sync("OpenUri", GLib.Variant("(s)", (uri,)), Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                print(f"OpenUri failed: {e}")

    def get_volume(self):
        if self.props_proxy:
            try:
                val = self.props_proxy.call_sync(
                    "Get",
                    GLib.Variant("(ss)", (PLAYER_INTERFACE, "Volume")),
                    Gio.DBusCallFlags.NONE,
                    -1,
                    None
                ).unpack()[0]
                return float(val)
            except Exception:
                pass
        return 0.7

    def set_volume(self, val):
        if self.props_proxy:
            try:
                val = max(0.0, min(1.0, float(val)))
                params = GLib.Variant("(ssv)", (PLAYER_INTERFACE, "Volume", GLib.Variant("d", val)))
                self.props_proxy.call_sync("Set", params, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                print(f"Set volume failed: {e}")

    def adjust_volume(self, delta):
        curr = self.get_volume()
        self.set_volume(curr + delta)

    def raise_spotify(self):
        if self.root_proxy:
            try:
                self.root_proxy.call_sync("Raise", None, Gio.DBusCallFlags.NONE, -1, None)
            except Exception as e:
                print(f"Raise failed: {e}")
