import os
import re
import glob
import json
import time
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor
from gi.repository import GLib

try:
    from i18n import _
except ImportError:
    def _(k, **kwargs):
        return k

CACHE_DIR = os.path.expanduser("~/.cache/spotify-mini-player")

def is_valid_name(name):
    if not name or not isinstance(name, str):
        return False
    name = name.strip()
    if not name or name in ("Очередь", "Queue", "...", "…", "Нет трека", "No track"):
        return False
    if not any(c.isalnum() for c in name):
        return False
    if not all(c.isprintable() for c in name):
        return False
    return True

def decode_varint(data, offset):
    res = 0
    shift = 0
    idx = offset
    while idx < len(data):
        b = data[idx]
        res |= (b & 0x7f) << shift
        idx += 1
        if not (b & 0x80):
            break
        shift += 7
    return res, idx

def read_ldb_clean(fpath):
    """Reads file, automatically removing 7-byte LevelDB WAL block headers every 32768 bytes for .log files."""
    try:
        with open(fpath, "rb") as fp:
            raw = fp.read()
    except Exception:
        return b""
    if not fpath.endswith(".log"):
        return raw
    BLOCK_SIZE = 32768
    HEADER_SIZE = 7
    if len(raw) <= BLOCK_SIZE:
        return raw[HEADER_SIZE:] if len(raw) > HEADER_SIZE else raw
    clean_blocks = []
    for offset in range(0, len(raw), BLOCK_SIZE):
        blk = raw[offset:offset + BLOCK_SIZE]
        clean_blocks.append(blk[HEADER_SIZE:] if len(blk) > HEADER_SIZE else blk)
    return b"".join(clean_blocks)

class QueueManager:
    """Dynamically detects and syncs the REAL Spotify queue, playlist, or album in real time."""
    def __init__(self, on_queue_changed_cb=None):
        self.on_queue_changed_cb = on_queue_changed_cb
        self.current_track = None
        self.context_name = ""
        self.context_uri = ""
        self.all_context_tracks = []
        self.session_history = []
        self.context_cache = {}  # {uri_or_id: (name, tracks)}
        self.track_meta_cache = {}  # {track_id: {title, artist, uri}}
        self._fetching = False
        self._last_ldb_mtime = 0
        self._last_browser_ldb_mtime = 0
        self._last_sort_state = None
        self._cached_sort_states = {}
        self._cached_user_id = None
        self._user_owned_cache = {}
        self._active_sp_pid_cache = None
        self._active_sp_pid_time = 0
        self._lock = threading.Lock()
        self.load_cache()

    def _get_user_spotify_id(self):
        if getattr(self, "_cached_user_id", None):
            return self._cached_user_id
        user_dirs = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user"))
        if user_dirs:
            uid = os.path.basename(user_dirs[0]).replace("-user", "").strip()
            self._cached_user_id = uid
            return uid
        self._cached_user_id = ""
        return ""

    def _is_user_owned_playlist(self, playlist_id):
        if not playlist_id:
            return False
        if hasattr(self, "_user_owned_cache") and playlist_id in self._user_owned_cache:
            return self._user_owned_cache[playlist_id]
        uid = self._get_user_spotify_id()
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        all_files = sorted([f for f in files if f.endswith(".log")], key=os.path.getmtime, reverse=True) + \
                    sorted([f for f in files if f.endswith(".ldb")], key=os.path.getmtime, reverse=True)
        target = f"pl#members#\x27spotify:playlist:{playlist_id}".encode()
        is_user = False
        for f in all_files:
            try:
                d = read_ldb_clean(f)
                if target in d:
                    idx = d.rfind(target)
                    chunk = d[idx:idx + 400]
                    if uid and uid.encode() in chunk:
                        is_user = True
                        break
            except Exception:
                pass
        if not is_user and uid:
            try:
                tracks = self._extract_playlist_from_ldb(playlist_id)
                if any(t.get("added_by") == uid for t in tracks):
                    is_user = True
            except Exception:
                pass
        if not hasattr(self, "_user_owned_cache"):
            self._user_owned_cache = {}
        self._user_owned_cache[playlist_id] = is_user
        return is_user

    def _get_active_spotify_playlist(self):
        """Finds the active playlist context directly from Spotify's LevelDB rp#ctx or yl#rpp records."""
        now = time.time()
        if hasattr(self, "_active_sp_pid_time") and now - getattr(self, "_active_sp_pid_time", 0) < 1.0:
            return getattr(self, "_active_sp_pid_cache", None)
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        all_files = sorted([f for f in files if f.endswith(".log")], key=os.path.getmtime, reverse=True) + \
                    sorted([f for f in files if f.endswith(".ldb")], key=os.path.getmtime, reverse=True)
        res = None
        for f in all_files:
            try:
                d = read_ldb_clean(f)
                matches = list(re.finditer(rb"(?:1!rp#ctx|1!yl#rpp)#[^\x00]*?\x27spotify:playlist:([a-zA-Z0-9]{22})", d))
                if matches:
                    res = matches[-1].group(1).decode()
                    break
            except Exception:
                pass
        self._active_sp_pid_cache = res
        self._active_sp_pid_time = now
        return res

    def _lookup_track_meta(self, tid):
        if not tid:
            return None
        return self.track_meta_cache.get(tid)

    def load_cache(self):
        """Restores cached queue state, metadata, and active playlist context.
        If a user playlist was active, attempts to re-extract fresh track order from LevelDB."""
        cache_file = os.path.join(CACHE_DIR, "queue_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    self.track_meta_cache = data.get("track_meta_cache", {})
                    # Clean any legacy bogus "Spotify" artist entries
                    for k, v in self.track_meta_cache.items():
                        if (v.get("artist") or "").strip().casefold() == "spotify":
                            v["artist"] = ""

                    c_name = data.get("context_name", "")
                    self.context_name = c_name if is_valid_name(c_name) else ""
                    self.context_uri = data.get("context_uri", "")
                    self.all_context_tracks = data.get("all_context_tracks", [])
                    self.session_history = data.get("session_history", [])
                    self.context_cache = data.get("context_cache", {})
                    self.last_valid_idx = data.get("last_valid_idx", -1)
                    self._last_sort_state = data.get("sort_state", None)

                    for t in self.all_context_tracks:
                        if (t.get("artist") or "").strip().casefold() == "spotify":
                            t["artist"] = ""

                    # If active context was a playlist, refresh tracks and name from LevelDB in real time:
                    if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
                        pid = self.context_uri.split(":")[-1]
                        if self._last_sort_state:
                            self._cached_sort_states[pid] = self._last_sort_state
                        ldb_name = self._get_playlist_name_from_ldb(pid)
                        if is_valid_name(ldb_name):
                            self.context_name = ldb_name
                        active_sort = self._get_playlist_sort_state(pid)
                        if active_sort is not None:
                            self._last_sort_state = active_sort
                        fresh_tracks = self._extract_playlist_from_ldb(pid)
                        if fresh_tracks:
                            self.all_context_tracks = fresh_tracks

                    # Check if cache had an album or collapsed state while Spotify was actively playing a user playlist
                    active_sp_pid = self._get_active_spotify_playlist()
                    if active_sp_pid and (not self.context_uri or not self.context_uri.startswith("spotify:playlist:") or len(self.all_context_tracks) <= 1 or not self._is_user_owned_playlist(self.context_uri.split(":")[-1])):
                        fresh_tracks = self._extract_playlist_from_ldb(active_sp_pid)
                        if fresh_tracks and len(fresh_tracks) > 1:
                            self.context_uri = f"spotify:playlist:{active_sp_pid}"
                            ldb_name = self._get_playlist_name_from_ldb(active_sp_pid)
                            if is_valid_name(ldb_name):
                                self.context_name = ldb_name
                            self.all_context_tracks = fresh_tracks

                    elif not self.context_uri or not self.all_context_tracks or len(self.all_context_tracks) <= 1:
                        # Fallback cache recovery
                        last_t = self.session_history[-1] if self.session_history else None
                        last_tid = self._extract_id(last_t.get("uri")) if last_t else None
                        recovered_pl = self._find_active_playlist_for_track(last_tid) if last_tid else None
                        if not recovered_pl:
                            files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
                            all_files = sorted([f for f in files if f.endswith(".log")], key=os.path.getmtime, reverse=True) + \
                                        sorted([f for f in files if f.endswith(".ldb")], key=os.path.getmtime, reverse=True)
                            for f in all_files:
                                try:
                                    d = read_ldb_clean(f)
                                    for m in re.finditer(rb"1!pl#slc#\x27spotify:playlist:([a-zA-Z0-9]{22})#", d):
                                        c_pid = m.group(1).decode()
                                        if self._is_user_owned_playlist(c_pid):
                                            recovered_pl = f"spotify:playlist:{c_pid}"
                                            break
                                    if recovered_pl:
                                        break
                                except Exception:
                                    pass
                        if recovered_pl:
                            pid = recovered_pl.split(":")[-1]
                            pl_name = self._get_playlist_name_from_ldb(pid)
                            fresh_tracks = self._extract_playlist_from_ldb(pid)
                            if fresh_tracks and len(fresh_tracks) > 1:
                                self.context_uri = recovered_pl
                                if is_valid_name(pl_name):
                                    self.context_name = pl_name
                                self.all_context_tracks = fresh_tracks
            except Exception as e:
                print(f"Error loading queue cache: {e}")

    def save_cache(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            cache_file = os.path.join(CACHE_DIR, "queue_cache.json")
            # Guard against overwriting an authentic user playlist cache with an album or transient 1-track collapse
            if os.path.exists(cache_file):
                try:
                    with open(cache_file, "r", encoding="utf-8") as f_prev:
                        prev = json.load(f_prev)
                        prev_tracks = prev.get("all_context_tracks", [])
                        prev_uri = prev.get("context_uri", "")
                        if len(prev_tracks) > 1 and prev_uri.startswith("spotify:playlist:"):
                            prev_pid = prev_uri.split(":")[-1]
                            if self._is_user_owned_playlist(prev_pid):
                                # If current context is collapsed to <= 1 track, NEVER overwrite a good multi-track user playlist!
                                if len(self.all_context_tracks) <= 1:
                                    return
                                # If current context is NOT a user playlist:
                                if not (self.context_uri and self.context_uri.startswith("spotify:playlist:") and self._is_user_owned_playlist(self.context_uri.split(":")[-1])):
                                    curr_id = self._extract_id(self.current_track.get("uri")) if self.current_track else None
                                    active_sp_pid = self._get_active_spotify_playlist()
                                    if active_sp_pid == prev_pid or (curr_id and any(t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id for t in prev_tracks)):
                                        return
                except Exception:
                    pass
            data = {
                "context_name": self.context_name if is_valid_name(self.context_name) else "",
                "context_uri": self.context_uri,
                "all_context_tracks": self.all_context_tracks,
                "session_history": self.session_history,
                "context_cache": self.context_cache,
                "track_meta_cache": self.track_meta_cache,
                "sort_state": self._last_sort_state,
                "last_valid_idx": getattr(self, "last_valid_idx", -1)
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving queue cache: {e}")

    def notify(self, order_changed=False):
        self.save_cache()
        if self.on_queue_changed_cb:
            try:
                self.on_queue_changed_cb(order_changed=order_changed)
            except TypeError:
                self.on_queue_changed_cb()

    def _find_track_idx(self, uri, title=""):
        if not self.all_context_tracks:
            return -1
        ref_idx = getattr(self, "last_valid_idx", 0)
        curr_id = self._extract_id(uri)
        if curr_id:
            matches = [idx for idx, t in enumerate(self.all_context_tracks) if t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id]
            if matches:
                if len(matches) > 1:
                    matches.sort(key=lambda i: abs(i - ref_idx))
                return matches[0]
        if title:
            norm_title = title.strip().lower()
            if norm_title and norm_title not in ("трек", "нет трека", "track", "no track"):
                matches = []
                for idx, t in enumerate(self.all_context_tracks):
                    t_title = t.get("title", "").strip().lower()
                    if t_title and t_title not in ("трек", "track", "") and t_title == norm_title:
                        matches.append(idx)
                if matches:
                    matches.sort(key=lambda i: abs(i - ref_idx))
                    return matches[0]
        return -1

    def get_past_tracks(self, limit=40, loop=True):
        """Returns tracks that come BEFORE the current track in this playlist, wrapping if loop=True."""
        if self.all_context_tracks:
            curr_idx = -1
            if self.current_track:
                curr_idx = self._find_track_idx(
                    self.current_track.get("uri", ""),
                    self.current_track.get("title", "")
                )
            if curr_idx < 0:
                curr_idx = getattr(self, "last_valid_idx", -1)

            if curr_idx >= 0:
                self.last_valid_idx = curr_idx
                total = len(self.all_context_tracks)
                if total > 0:
                    past_before = self.all_context_tracks[max(0, curr_idx - limit):curr_idx]
                    if loop and limit and len(past_before) < limit and total > 1:
                        wrap_needed = limit - len(past_before)
                        start_w = max(curr_idx + 1, total - wrap_needed)
                        wrapped = self.all_context_tracks[start_w:]
                        return wrapped + past_before
                    return past_before
            else:
                # If current_track is temporarily off-playlist, preserve past tracks using last_valid_idx
                l_idx = getattr(self, "last_valid_idx", 0)
                if 0 < l_idx < len(self.all_context_tracks):
                    start_i = max(0, l_idx - limit) if limit else 0
                    return self.all_context_tracks[start_i:l_idx]
        return []

    def get_upcoming_tracks(self, limit=60, loop=True):
        """Returns upcoming tracks in the current playlist in order, wrapping if loop=True."""
        if self.all_context_tracks:
            curr_idx = -1
            if self.current_track:
                curr_idx = self._find_track_idx(
                    self.current_track.get("uri", ""),
                    self.current_track.get("title", "")
                )
            if curr_idx < 0:
                curr_idx = getattr(self, "last_valid_idx", -1)

            if curr_idx >= 0:
                self.last_valid_idx = curr_idx
                total = len(self.all_context_tracks)
                if total > 0:
                    tracks_after = self.all_context_tracks[curr_idx + 1:]
                    if loop and limit and len(tracks_after) < limit and total > 1:
                        wrap_needed = limit - len(tracks_after)
                        wrapped = self.all_context_tracks[:min(wrap_needed, curr_idx)]
                        return tracks_after + wrapped
                    return tracks_after[:limit] if limit else tracks_after
            else:
                # If current_track is temporarily off-playlist, preserve upcoming tracks using last_valid_idx
                l_idx = getattr(self, "last_valid_idx", 0)
                if l_idx + 1 < len(self.all_context_tracks):
                    end_i = l_idx + 1 + limit if limit else len(self.all_context_tracks)
                    return self.all_context_tracks[l_idx + 1:end_i]
                elif self.all_context_tracks:
                    return self.all_context_tracks[:limit] if limit else self.all_context_tracks
        return []

    def _get_playlist_name_from_ldb(self, playlist_id):
        """Extracts playlist name directly from Spotify LevelDB attribute record in <1ms."""
        if not playlist_id:
            return ""
        target = ("2!pl#attr#\x27spotify:playlist:" + playlist_id + "#").encode()
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        log_files = sorted([f for f in files if f.endswith(".log")], key=os.path.getmtime, reverse=True)
        ldb_files = sorted([f for f in files if f.endswith(".ldb")], key=os.path.getmtime, reverse=True)
        for fpath in log_files + ldb_files:
            try:
                d = read_ldb_clean(fpath)
                pos = len(d)
                while True:
                    idx = d.rfind(target, 0, pos)
                    if idx == -1:
                        break
                    p = idx + len(target)
                    p_tag = d.find(b"\n", p, p + 15)
                    if p_tag != -1:
                        str_len, nxt = decode_varint(d, p_tag + 1)
                        if 1 <= str_len <= 150 and nxt + str_len <= len(d):
                            val = d[nxt:nxt + str_len].decode("utf-8", errors="ignore").strip()
                            if is_valid_name(val):
                                return val
                    pos = idx
            except Exception:
                pass
        return ""

    def get_context_name(self, current_album=""):
        # 1. From context_uri if playlist - ALWAYS check real playlist name in LevelDB first!
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            pid = self.context_uri.split(":")[-1]
            ldb_name = self._get_playlist_name_from_ldb(pid)
            if is_valid_name(ldb_name):
                self.context_name = ldb_name
                return self.context_name
            if self.context_uri in self.context_cache:
                c_name = self.context_cache[self.context_uri][0]
                if is_valid_name(c_name):
                    self.context_name = c_name
                    return self.context_name
            if is_valid_name(self.context_name):
                return self.context_name

        # If we currently have an active multi-track playlist, never fall back to album!
        if len(self.all_context_tracks) > 1 and is_valid_name(self.context_name):
            return self.context_name

        # 2. Try detecting active context from restore state or LevelDB (READ-ONLY, never mutate state)
        curr_id = self._extract_id(self.current_track.get("uri")) if self.current_track else None
        detected = self._detect_active_context_uri(curr_id)
        if detected:
            if detected.startswith("spotify:playlist:"):
                pid = detected.split(":")[-1]
                ldb_name = self._get_playlist_name_from_ldb(pid)
                if is_valid_name(ldb_name):
                    return ldb_name
            elif detected.startswith("spotify:album:") and current_album:
                if not (self.context_uri and self.context_uri.startswith("spotify:playlist:")) and len(self.all_context_tracks) <= 1:
                    if is_valid_name(current_album):
                        return current_album

        # 3. Fallback to current album if playing an album (never if playing a playlist)
        if not (self.context_uri and self.context_uri.startswith("spotify:playlist:")) and len(self.all_context_tracks) <= 1:
            if current_album and is_valid_name(current_album):
                return current_album

        return self.context_name or ""

    def check_for_updates(self):
        """Checks if active playlist or sort order was modified in Spotify LevelDB and refreshes if needed."""
        now = time.time()
        if now - getattr(self, "_last_update_check_time", 0) < 2.0:
            return
        self._last_update_check_time = now

        curr_id = self._extract_id(self.current_track.get("uri")) if self.current_track else None
        detected = self._detect_active_context_uri(curr_id)
        if detected and self.context_uri and detected != self.context_uri:
            # If playing a playlist, album/track detection or track being in current playlist must NOT trigger context reset:
            if self.context_uri.startswith("spotify:playlist:"):
                t_uri = self.current_track.get("uri", "") if self.current_track else ""
                t_title = self.current_track.get("title", "") if self.current_track else ""
                if self._find_track_idx(t_uri, t_title) >= 0 or detected.startswith("spotify:track:") or detected.startswith("spotify:album:"):
                    pass
                elif detected.startswith("spotify:playlist:"):
                    # Only switch if track is actually in the new playlist
                    new_pid = detected.split(":")[-1]
                    new_tracks = self._extract_playlist_from_ldb(new_pid)
                    if new_tracks and any(t.get("tid") == curr_id for t in new_tracks):
                        if self.current_track:
                            self.update_current_track(
                                self.current_track.get("title", ""),
                                self.current_track.get("artist", ""),
                                self.current_track.get("uri", ""),
                                self.current_track.get("album", "")
                            )
                        return
            else:
                if self.current_track:
                    self.update_current_track(
                        self.current_track.get("title", ""),
                        self.current_track.get("artist", ""),
                        self.current_track.get("uri", ""),
                        self.current_track.get("album", "")
                    )
                return

        if not self.context_uri or not self.context_uri.startswith("spotify:playlist:"):
            return
        pid = self.context_uri.split(":")[-1]

        sort_changed = False
        try:
            cur_sort = self._get_playlist_sort_state(pid)
            if cur_sort is not None and cur_sort != self._last_sort_state:
                print(f"[QueueManager] Sort state changed for {pid}: {self._last_sort_state} -> {cur_sort}")
                self._last_sort_state = cur_sort
                sort_changed = True
            elif cur_sort is None and self._last_sort_state is not None:
                if pid in self._cached_sort_states and self._cached_sort_states[pid] is None:
                    print(f"[QueueManager] Sort state cleared for {pid}: {self._last_sort_state} -> None")
                    self._last_sort_state = None
                    sort_changed = True

            ldb_name = self._get_playlist_name_from_ldb(pid)
            if is_valid_name(ldb_name) and ldb_name != self.context_name:
                self.context_name = ldb_name

            # If sort changed or all_context_tracks is empty, reload tracks
            if sort_changed or not self.all_context_tracks:
                fresh_tracks = self._extract_playlist_from_ldb(pid)
                if fresh_tracks:
                    self._apply_fresh_tracks(fresh_tracks, order_changed=True)
                return

            # Check if tracks were added/removed in Spotify
            log_files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*.log")) + \
                        glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*.ldb"))
            if log_files:
                latest_ldb_mtime = max(os.path.getmtime(f) for f in log_files)
                if latest_ldb_mtime > self._last_ldb_mtime:
                    self._last_ldb_mtime = latest_ldb_mtime
                    fresh_tracks = self._extract_playlist_from_ldb(pid)
                    if fresh_tracks:
                        old_tids = [t.get("tid") for t in self.all_context_tracks]
                        new_tids = [t.get("tid") for t in fresh_tracks]
                        if old_tids != new_tids and abs(len(fresh_tracks) - len(self.all_context_tracks)) > 0:
                            self._apply_fresh_tracks(fresh_tracks, order_changed=True)
        except Exception as e:
            print(f"Error checking for playlist updates: {e}")

    def force_refresh_context(self, title="", artist="", uri="", album=""):
        """Forces an immediate re-read of active Spotify context and queue tracks."""
        t = title or (self.current_track.get("title", "") if self.current_track else "")
        a = artist or (self.current_track.get("artist", "") if self.current_track else "")
        u = uri or (self.current_track.get("uri", "") if self.current_track else "")
        alb = album or (self.current_track.get("album", "") if self.current_track else "")
        curr_id = self._extract_id(u)

        prev_pid = self.context_uri.split(":")[-1] if (self.context_uri and self.context_uri.startswith("spotify:playlist:")) else None

        active_ctx = self._detect_active_context_uri(curr_id)
        if active_ctx and active_ctx in self.context_cache:
            del self.context_cache[active_ctx]
        if self.context_uri and self.context_uri in self.context_cache:
            del self.context_cache[self.context_uri]

        if prev_pid:
            self._cached_sort_states.pop(prev_pid, None)
            self._last_sort_state = None
        if curr_id:
            self.context_cache.pop(f"track_album:{curr_id}", None)

        # If active context was a playlist, immediately reload fresh tracks from LevelDB
        if prev_pid:
            fresh_tracks = self._extract_playlist_from_ldb(prev_pid)
            if fresh_tracks:
                ldb_name = self._get_playlist_name_from_ldb(prev_pid)
                with self._lock:
                    self.context_uri = f"spotify:playlist:{prev_pid}"
                    if is_valid_name(ldb_name):
                        self.context_name = ldb_name
                    self.all_context_tracks = fresh_tracks
                self.update_current_track(t, a, u, alb)
                return

        with self._lock:
            self.context_uri = ""
            self.all_context_tracks = []

        self.update_current_track(t, a, u, alb)

    def _apply_fresh_tracks(self, fresh_tracks, order_changed=True):
        for t in fresh_tracks:
            tid = t.get("tid")
            cached = self._lookup_track_meta(tid)
            if cached:
                t["title"] = cached.get("title", t.get("title"))
                t["artist"] = cached.get("artist", t.get("artist"))
                t["album"] = cached.get("album", t.get("album", ""))
                t["duration"] = cached.get("duration", t.get("duration", 0))
        with self._lock:
            self.all_context_tracks = fresh_tracks
            if self.current_track:
                new_idx = self._find_track_idx(self.current_track.get("uri", ""), self.current_track.get("title", ""))
                if new_idx >= 0:
                    self.current_track["track_num"] = new_idx + 1
                    self.last_valid_idx = new_idx
        self.notify(order_changed=order_changed)


    def update_current_track(self, title, artist, uri, album=""):
        if not title:
            return

        # Normalize URI format
        if uri and uri.startswith("/com/spotify/track/"):
            uri = "spotify:track:" + uri.split("/")[-1]

        curr_id = self._extract_id(uri)
        if curr_id:
            curr_meta = self.track_meta_cache.setdefault(curr_id, {})
            curr_meta["title"] = title
            curr_meta["artist"] = artist
            curr_meta["uri"] = uri
            if album:
                curr_meta["album"] = album

        old_track = self.current_track
        norm_title = title.strip().lower()

        # Check if song changed
        if old_track and old_track.get("title") and old_track.get("title", "").strip().lower() != norm_title:
            if not self.session_history or self.session_history[-1].get("title", "").strip().lower() != old_track["title"].strip().lower():
                self.session_history.append(dict(old_track))
                if len(self.session_history) > 40:
                    self.session_history.pop(0)

        # Check if sort order of active playlist changed
        order_changed = False
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            cur_pid = self.context_uri.split(":")[-1]
            active_sort = self._get_playlist_sort_state(cur_pid)
            if active_sort != self._last_sort_state:
                self._last_sort_state = active_sort
                order_changed = True
                fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
                if fresh_tracks:
                    for t in fresh_tracks:
                        tid = t.get("tid")
                        cached = self._lookup_track_meta(tid)
                        if cached:
                            t["title"] = cached.get("title", t.get("title"))
                            t["artist"] = cached.get("artist", t.get("artist"))
                            t["album"] = cached.get("album", t.get("album", ""))
                            t["duration"] = cached.get("duration", t.get("duration", 0))
                    with self._lock:
                        self.all_context_tracks = fresh_tracks

        # Single detection
        norm_t = title.strip().casefold()
        norm_a = album.strip().casefold()
        is_single = bool(norm_t and norm_a and (
            norm_a == norm_t or 
            norm_a in (f"{norm_t} - single", f"{norm_t} (single)", f"{norm_t} [single]") or
            norm_a.endswith(" - single") or
            norm_a.endswith(" (single)")
        ))

        # Check if current track belongs to our active playlist
        is_in_active_playlist = False
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            if self._find_track_idx(uri, title) >= 0:
                is_in_active_playlist = True
            else:
                cur_pid = self.context_uri.split(":")[-1]
                fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
                if fresh_tracks and any((curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().casefold() == norm_title) for t in fresh_tracks):
                    self._apply_fresh_tracks(fresh_tracks, order_changed=True)
                    is_in_active_playlist = True

        detected_ctx = self._detect_active_context_uri(curr_id)

        # If not in active playlist, check if track belongs to an active user playlist from Browser Local Storage
        if not is_in_active_playlist and curr_id:
            active_pl = self._find_active_playlist_for_track(curr_id)
            if active_pl:
                detected_ctx = active_pl

        # Determine whether the playback context has changed
        context_has_changed = False
        if is_in_active_playlist:
            # Current track is inside our active playlist.
            # Never switch context away from the user's active playlist!
            context_has_changed = False
        else:
            if detected_ctx:
                if not self.context_uri or detected_ctx != self.context_uri:
                    context_has_changed = True
            elif not self.context_uri:
                context_has_changed = True

        # If previous context was a single (1 track), and incoming track has an album and is not a single:
        if len(self.all_context_tracks) <= 1 and not (self.context_uri and self.context_uri.startswith("spotify:playlist:")) and is_valid_name(album) and not is_single:
            context_has_changed = True

        # If previous context was an album, and MPRIS reports a different album name:
        if self.context_uri and self.context_uri.startswith("spotify:album:") and is_valid_name(album):
            if is_valid_name(self.context_name) and album.strip().casefold() != self.context_name.strip().casefold():
                context_has_changed = True

        # 1. If context did NOT change, check if current track is already within our active context tracks
        if not context_has_changed:
            found_idx = self._find_track_idx(uri, title)
            if found_idx >= 0:
                self.last_valid_idx = found_idx
                t = self.all_context_tracks[found_idx]
                if t.get("title") in ("Трек", "", None, "Track"):
                    t["title"] = title
                    t["artist"] = artist
                if album:
                    t["album"] = album
                self.current_track = {
                    "title": title,
                    "artist": artist,
                    "uri": uri,
                    "album": album,
                    "track_num": found_idx + 1
                }
                if not (self.context_uri and self.context_uri.startswith("spotify:playlist:")):
                    if is_valid_name(album):
                        self.context_name = album
                elif not is_valid_name(self.context_name):
                    self.get_context_name(current_album=album)
                self.notify(order_changed=order_changed)
                self._resolve_missing_tracks_async(found_idx)
                return

            # Case A: Track might have been newly added to the CURRENT playlist (e.g. 17 nozhevyh)
            if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
                cur_pid = self.context_uri.split(":")[-1]
                fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
                fresh_idx = -1
                if fresh_tracks:
                    for idx, t in enumerate(fresh_tracks):
                        if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (not curr_id and t.get("title", "").strip().lower() == norm_title):
                            fresh_idx = idx
                            t["title"] = title
                            t["artist"] = artist
                            break
                if fresh_idx >= 0:
                    with self._lock:
                        self.all_context_tracks = fresh_tracks
                        self.last_valid_idx = fresh_idx
                        self.current_track = {
                            "title": title,
                            "artist": artist,
                            "uri": uri,
                            "album": album,
                            "track_num": fresh_idx + 1
                        }
                        if not is_valid_name(self.context_name):
                            self.get_context_name(current_album=album)
                    self.notify()
                    self._resolve_missing_tracks_async(fresh_idx)
                    return

        # If we have an active playlist and track wasn't found immediately, do one more check before abandoning
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            cur_pid = self.context_uri.split(":")[-1]
            fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
            if fresh_tracks and any((curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().casefold() == norm_title) for t in fresh_tracks):
                self._apply_fresh_tracks(fresh_tracks, order_changed=True)
                return

        # Check active container context from Spotify's context_player_state_restore:
        if detected_ctx:
            if detected_ctx.startswith("spotify:track:"):
                # If current track is part of an active user playlist, load that playlist!
                if curr_id:
                    active_pl = self._find_active_playlist_for_track(curr_id)
                    if active_pl:
                        new_pid = active_pl.split(":")[-1]
                        fresh_tracks = self._extract_playlist_from_ldb(new_pid)
                        if fresh_tracks:
                            fresh_idx = -1
                            for idx, t in enumerate(fresh_tracks):
                                if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                                    fresh_idx = idx
                                    t["title"] = title
                                    t["artist"] = artist
                                    break
                            if fresh_idx >= 0:
                                pl_name = self._get_playlist_name_from_ldb(new_pid)
                                with self._lock:
                                    self.context_uri = active_pl
                                    if is_valid_name(pl_name):
                                        self.context_name = pl_name
                                    self.all_context_tracks = fresh_tracks
                                    self.last_valid_idx = fresh_idx
                                    self.current_track = {
                                        "title": title,
                                        "artist": artist,
                                        "uri": uri,
                                        "album": album,
                                        "track_num": fresh_idx + 1
                                    }
                                self.notify(order_changed=True)
                                self._sync_context_async(curr_id, title, artist, album)
                                self._resolve_missing_tracks_async(fresh_idx)
                                return

                # If single, maintain clean 1-track context
                if is_single:
                    # Do not overwrite if we already have an active playlist
                    if (self.context_uri and self.context_uri.startswith("spotify:playlist:")) or len(self.all_context_tracks) > 1:
                        return
                    single_t = {
                        "track_num": 1,
                        "title": title,
                        "artist": artist,
                        "uri": uri,
                        "album": album,
                        "tid": curr_id
                    }
                    with self._lock:
                        self.context_uri = detected_ctx
                        self.context_name = title if is_valid_name(title) else _("single")
                        self.all_context_tracks = [single_t]
                        self.last_valid_idx = 0
                        self.current_track = single_t
                    self.notify(order_changed=True)
                    return

                # Not a single - try fetching track's album
                if curr_id and is_valid_name(album):
                    name, emb_tracks, alb_id = self._fetch_album_for_track(curr_id)
                    if emb_tracks:
                        fresh_idx = -1
                        for idx, t in enumerate(emb_tracks):
                            if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                                fresh_idx = idx
                                break
                        if fresh_idx >= 0:
                            with self._lock:
                                self.context_uri = f"spotify:album:{alb_id}" if alb_id else f"spotify:album:{curr_id}"
                                self.context_name = name or album or title
                                self.all_context_tracks = emb_tracks
                                self.last_valid_idx = fresh_idx
                                self.current_track = {
                                    "title": title,
                                    "artist": artist,
                                    "uri": uri,
                                    "album": album,
                                    "track_num": fresh_idx + 1
                                }
                            self.notify(order_changed=True)
                            return

                # Fallback if album couldn't be fetched: NEVER overwrite active playlist with a single track
                if (self.context_uri and self.context_uri.startswith("spotify:playlist:")) or len(self.all_context_tracks) > 1:
                    return

                single_t = {
                    "track_num": 1,
                    "title": title,
                    "artist": artist,
                    "uri": uri,
                    "album": album,
                    "tid": curr_id
                }
                with self._lock:
                    self.context_uri = detected_ctx
                    self.context_name = title if is_valid_name(title) else _("single")
                    self.all_context_tracks = [single_t]
                    self.last_valid_idx = 0
                    self.current_track = single_t
                self.notify(order_changed=True)
                return

            elif detected_ctx.startswith("spotify:playlist:"):
                new_pid = detected_ctx.split(":")[-1]
                fresh_tracks = self._extract_playlist_from_ldb(new_pid)
                if fresh_tracks:
                    fresh_idx = -1
                    for idx, t in enumerate(fresh_tracks):
                        if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                            fresh_idx = idx
                            t["title"] = title
                            t["artist"] = artist
                            break
                    if fresh_idx >= 0:
                        pl_name = self._get_playlist_name_from_ldb(new_pid)
                        with self._lock:
                            self.context_uri = detected_ctx
                            if is_valid_name(pl_name):
                                self.context_name = pl_name
                            self.all_context_tracks = fresh_tracks
                            self.last_valid_idx = fresh_idx
                            self.current_track = {
                                "title": title,
                                "artist": artist,
                                "uri": uri,
                                "album": album,
                                "track_num": fresh_idx + 1
                            }
                        self.notify(order_changed=True)
                        self._sync_context_async(curr_id, title, artist, album)
                        self._resolve_missing_tracks_async(fresh_idx)
                        return
                else:
                    name, emb_tracks = self._fetch_embed_tracks(detected_ctx)
                    if emb_tracks:
                        fresh_idx = -1
                        for idx, t in enumerate(emb_tracks):
                            if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                                fresh_idx = idx
                                break
                        if fresh_idx >= 0:
                            with self._lock:
                                self.context_uri = detected_ctx
                                if is_valid_name(name):
                                    self.context_name = name
                                self.all_context_tracks = emb_tracks
                                self.last_valid_idx = fresh_idx
                                self.current_track = {
                                    "title": title,
                                    "artist": artist,
                                    "uri": uri,
                                    "album": album,
                                    "track_num": fresh_idx + 1
                                }
                            self.notify(order_changed=True)
                            return

            elif detected_ctx.startswith("spotify:album:"):
                name, emb_tracks = self._fetch_embed_tracks(detected_ctx)
                if emb_tracks:
                    fresh_idx = -1
                    for idx, t in enumerate(emb_tracks):
                        if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                            fresh_idx = idx
                            break
                    if fresh_idx >= 0:
                        with self._lock:
                            self.context_uri = detected_ctx
                            self.context_name = name or album or "Альбом"
                            self.all_context_tracks = emb_tracks
                            self.last_valid_idx = fresh_idx
                            self.current_track = {
                                "title": title,
                                "artist": artist,
                                "uri": uri,
                                "album": album,
                                "track_num": fresh_idx + 1
                            }
                        self.notify(order_changed=True)
                        return

        # Case B: Check if track belongs to an active user playlist
        if curr_id:
            active_pl = self._find_active_playlist_for_track(curr_id)
            if active_pl:
                new_pid = active_pl.split(":")[-1]
                fresh_tracks = self._extract_playlist_from_ldb(new_pid)
                if fresh_tracks:
                    fresh_idx = -1
                    for idx, t in enumerate(fresh_tracks):
                        if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                            fresh_idx = idx
                            t["title"] = title
                            t["artist"] = artist
                            break
                    if fresh_idx >= 0:
                        pl_name = self._get_playlist_name_from_ldb(new_pid)
                        with self._lock:
                            self.context_uri = active_pl
                            if is_valid_name(pl_name):
                                self.context_name = pl_name
                            self.all_context_tracks = fresh_tracks
                            self.last_valid_idx = fresh_idx
                            self.current_track = {
                                "title": title,
                                "artist": artist,
                                "uri": uri,
                                "album": album,
                                "track_num": fresh_idx + 1
                            }
                        self.notify(order_changed=True)
                        self._sync_context_async(curr_id, title, artist, album)
                        self._resolve_missing_tracks_async(fresh_idx)
                        return

        # If currently playing an active user playlist, NEVER overwrite it with an album or single fallback!
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            cur_pid = self.context_uri.split(":")[-1]
            if self._is_user_owned_playlist(cur_pid) and len(self.all_context_tracks) > 1:
                fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
                if fresh_tracks:
                    self._apply_fresh_tracks(fresh_tracks, order_changed=True)
                return

        # Try fetching album tracks for track (handles full albums as well as single releases)
        if curr_id and is_valid_name(album):
            name, emb_tracks, alb_id = self._fetch_album_for_track(curr_id)
            if emb_tracks:
                fresh_idx = -1
                for idx, t in enumerate(emb_tracks):
                    if (curr_id and (t.get("tid") == curr_id or self._extract_id(t.get("uri", "")) == curr_id)) or (t.get("title", "").strip().lower() == norm_title):
                        fresh_idx = idx
                        break
                if fresh_idx >= 0:
                    with self._lock:
                        self.context_uri = f"spotify:album:{alb_id}" if alb_id else f"spotify:album:{curr_id}"
                        self.context_name = name or album or title
                        self.all_context_tracks = emb_tracks
                        self.last_valid_idx = fresh_idx
                        self.current_track = {
                            "title": title,
                            "artist": artist,
                            "uri": uri,
                            "album": album,
                            "track_num": fresh_idx + 1
                        }
                    self.notify(order_changed=True)
                    return

        # If we currently have an active user playlist, DO NOT overwrite it with a 1-track fallback!
        if (self.context_uri and self.context_uri.startswith("spotify:playlist:")) or len(self.all_context_tracks) > 1:
            return

        # Case C: If track is a single and album couldn't be fetched, maintain clean 1-track single context
        if is_single:
            if (self.context_uri and self.context_uri.startswith("spotify:playlist:")) or len(self.all_context_tracks) > 1:
                return
            single_t = {
                "track_num": 1,
                "title": title,
                "artist": artist,
                "uri": uri,
                "album": album,
                "tid": curr_id
            }
            with self._lock:
                self.context_uri = f"spotify:track:{curr_id}" if curr_id else uri
                self.context_name = title if is_valid_name(title) else _("single")
                self.all_context_tracks = [single_t]
                self.last_valid_idx = 0
                self.current_track = {
                    "title": title,
                    "artist": artist,
                    "uri": uri,
                    "album": album,
                    "track_num": 1
                }
            self.notify(order_changed=True)
            return

        # Case E: Standalone single track fallback
        if (self.context_uri and self.context_uri.startswith("spotify:playlist:")) or len(self.all_context_tracks) > 1:
            return

        single_t = {
            "track_num": 1,
            "title": title,
            "artist": artist,
            "uri": uri,
            "album": album,
            "tid": curr_id
        }
        with self._lock:
            self.context_uri = f"spotify:track:{curr_id}" if curr_id else ""
            self.context_name = title if is_valid_name(title) else _("single")
            self.all_context_tracks = [single_t]
            self.last_valid_idx = 0
            self.current_track = single_t
        self.notify(order_changed=True)
        return

    def _extract_id(self, uri):
        if not uri:
            return ""
        return uri.split(":")[-1].split("/")[-1].split("?")[0]

    def _get_playlist_sort_state(self, playlist_id):
        """Reads user's active sorting preference for playlist_id from Spotify's Browser Local Storage."""
        if not playlist_id:
            return None
        browser_dir = os.path.expanduser("~/.cache/spotify/Browser/Local Storage/leveldb")
        if not os.path.exists(browser_dir):
            return self._cached_sort_states.get(playlist_id, None)

        files = sorted(
            glob.glob(os.path.join(browser_dir, "*.log")) + glob.glob(os.path.join(browser_dir, "*.ldb")),
            key=os.path.getmtime,
            reverse=True
        )
        if not files:
            return self._cached_sort_states.get(playlist_id, None)

        target = f"spotify:playlist:{playlist_id}"
        for fpath in files:
            try:
                with open(fpath, "rb") as fp:
                    d = fp.read()
            except Exception:
                continue

            pos = len(d)
            while True:
                last_pos = d.rfind(b"sortedState", 0, pos)
                if last_pos == -1:
                    break
                pos = last_pos
                brace_start = d.find(b"{", last_pos, last_pos + 100)
                if brace_start == -1:
                    continue
                depth = 0
                end = -1
                for i in range(brace_start, min(brace_start + 8192, len(d))):
                    if d[i] == ord("{"):
                        depth += 1
                    elif d[i] == ord("}"):
                        depth -= 1
                        if depth == 0:
                            end = i + 1
                            break
                if end != -1:
                    try:
                        chunk = d[brace_start:end].decode("utf-8", errors="ignore")
                        data = json.loads(chunk)
                        if isinstance(data, dict) and target in data:
                            val = data[target]
                            if isinstance(val, dict) and val.get("field"):
                                self._cached_sort_states[playlist_id] = val
                                return val
                            elif val is None or val == {}:
                                self._cached_sort_states[playlist_id] = None
                                return None
                    except Exception:
                        pass

        return self._cached_sort_states.get(playlist_id, None)

    def _extract_playlist_from_ldb(self, playlist_id):
        """Extracts all tracks directly from Spotify's LevelDB slice in real authentic sequence."""
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        target = ("1!pl#slc#\x27spotify:playlist:" + playlist_id + "#").encode("utf-8")

        found_chunk = None
        for fpath in sorted(files, key=os.path.getmtime, reverse=True):
            if fpath.endswith(".log") or fpath.endswith(".ldb"):
                d = read_ldb_clean(fpath)
                matches = list(re.finditer(re.escape(target), d))
                if matches:
                    pos = matches[-1].start()
                    next_slice = d.find(b"1!pl#", pos + len(target))
                    found_chunk = d[pos:next_slice] if next_slice != -1 else d[pos:pos + 300000]
                    break

        if not found_chunk:
            return []

        entries = []
        seen = set()
        for m in re.finditer(rb"spotify:track:([a-zA-Z0-9]{22})", found_chunk):
            tid = m.group(1).decode()
            t_end = m.end()
            p = found_chunk.find(b"\x10", t_end, t_end + 60)
            added_at = 0
            added_by = ""
            if p != -1:
                added_at, _ = decode_varint(found_chunk, p + 1)
                user_slice = found_chunk[t_end:p]
                user_match = re.search(rb"[a-zA-Z0-9_-]{10,40}", user_slice)
                if user_match:
                    added_by = user_match.group(0).decode(errors="ignore")
            if tid not in seen:
                seen.add(tid)
                entries.append({"tid": tid, "uri": "spotify:track:" + tid, "added_at": added_at, "added_by": added_by})

        # Populate cached title / artist / album / duration
        for e in entries:
            cached = self._lookup_track_meta(e["tid"])
            if cached:
                e["title"] = cached.get("title") or e.get("title") or "Трек"
                artist_val = (cached.get("artist") or e.get("artist") or "").strip()
                e["artist"] = "" if artist_val.casefold() == "spotify" else artist_val
                e["album"] = cached.get("album") or e.get("album") or ""
                e["duration"] = cached.get("duration") or e.get("duration") or 0
            else:
                e["title"] = e.get("title", "Трек")
                e["artist"] = ""
                e["album"] = e.get("album", "")
                e["duration"] = e.get("duration", 0)

        # Apply active Spotify sort configuration for all columns
        sort_state = self._get_playlist_sort_state(playlist_id)
        if sort_state and isinstance(sort_state, dict):
            field = str(sort_state.get("field", "")).upper()
            order = str(sort_state.get("order", "ASC")).upper()
            reverse = (order == "DESC")
            if field == "ADDED_AT":
                entries.sort(key=lambda x: (x.get("added_at", 0), (x.get("title") or "").strip().casefold()), reverse=reverse)
            elif field in ("TITLE", "NAME"):
                entries.sort(key=lambda x: (
                    (x.get("title") or "").strip().casefold(),
                    (x.get("artist") or "").strip().casefold(),
                    x.get("added_at", 0)
                ), reverse=reverse)
            elif field == "ARTIST":
                entries.sort(key=lambda x: (
                    (x.get("artist") or "").strip().casefold(),
                    (x.get("album") or "").strip().casefold(),
                    (x.get("title") or "").strip().casefold(),
                    x.get("added_at", 0)
                ), reverse=reverse)
            elif field == "ALBUM":
                entries.sort(key=lambda x: (
                    (x.get("album") or "").strip().casefold() if (x.get("album") or "").strip() else (x.get("title") or "").strip().casefold(),
                    (x.get("artist") or "").strip().casefold(),
                    (x.get("title") or "").strip().casefold(),
                    x.get("added_at", 0)
                ), reverse=reverse)
            elif field in ("DURATION", "TIME"):
                entries.sort(key=lambda x: (x.get("duration", 0), x.get("added_at", 0)), reverse=reverse)
            elif field in ("ADDED_BY", "USER"):
                entries.sort(key=lambda x: ((x.get("added_by") or "").strip().casefold(), x.get("added_at", 0)), reverse=reverse)

        # Set 1-based playlist index
        for idx, e in enumerate(entries):
            e["track_num"] = idx + 1

        return entries

    def _fetch_track_info(self, track_id):
        if not track_id:
            return None
        cached = self._lookup_track_meta(track_id)
        if cached:
            has_title = bool(cached.get("title") and cached.get("title") not in ("Трек", "", None))
            has_artist = bool(cached.get("artist") and cached.get("artist").strip().casefold() != "spotify")
            has_album = bool(cached.get("album"))
            if has_title and has_artist and has_album:
                return cached

        if getattr(self, "_rate_limited_until", 0) > time.time():
            return cached

        # 1. Try embed API
        try:
            url = f"https://open.spotify.com/embed/track/{track_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r"<script id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", html)
                if m:
                    d = json.loads(m.group(1))
                    entity = d.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    t_name = entity.get("name")
                    artists_list = [a["name"] for a in entity.get("artists", []) if a.get("name") and a["name"] != "Spotify"]
                    a_name = ", ".join(artists_list) if artists_list else ""
                    album_obj = entity.get("album") or {}
                    alb_name = album_obj.get("name", "") if isinstance(album_obj, dict) else ""
                    dur = entity.get("duration", 0)
                    if t_name:
                        info = {"title": t_name, "artist": a_name, "uri": f"spotify:track:{track_id}", "album": alb_name, "duration": dur}
                        self.track_meta_cache[track_id] = info
                        return info
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self._rate_limited_until = time.time() + 90
        except Exception:
            pass

        # 2. If album or artist was not in embed, try open.spotify.com/track/{track_id}
        try:
            url = f"https://open.spotify.com/track/{track_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=3.5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
            title = ""
            artist = ""
            album = ""
            m_t = re.search(r"<meta property=\"og:title\" content=\"([^\"]+)\"", html)
            if m_t:
                title = m_t.group(1).strip()
            m_d = re.search(r"<meta name=\"twitter:description\" content=\"([^\"]+)\"", html)
            if m_d:
                parts = m_d.group(1).split(" · ")
                if len(parts) >= 3:
                    artist = parts[0].strip()
                    if artist.casefold() == "spotify":
                        artist = ""
                    album = parts[1].strip()
            if title:
                info = {"title": title, "artist": artist, "uri": f"spotify:track:{track_id}", "album": album, "duration": 0}
                self.track_meta_cache[track_id] = info
                return info
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self._rate_limited_until = time.time() + 90
        except Exception:
            pass

        # 3. Fallback to oembed (never hardcode Spotify as artist)
        try:
            o_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
            req = urllib.request.Request(o_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode())
                t_name = d.get("title")
                a_name = (d.get("author_name") or "").strip()
                if a_name.casefold() == "spotify":
                    a_name = ""
                if t_name:
                    info = {"title": t_name, "artist": a_name, "uri": f"spotify:track:{track_id}", "album": "", "duration": 0}
                    self.track_meta_cache[track_id] = info
                    return info
        except urllib.error.HTTPError as e:
            if e.code == 429:
                self._rate_limited_until = time.time() + 90
        except Exception:
            pass

        return cached

    def _resolve_missing_tracks_async(self, curr_idx=None):
        if not self.all_context_tracks:
            return

        def resolver():
            time.sleep(1.0)
            with self._lock:
                tracks = self.all_context_tracks
                n_tracks = len(tracks)

            if n_tracks == 0:
                return

            c_idx = curr_idx
            if c_idx is None:
                if self.current_track:
                    curr_id = self._extract_id(self.current_track.get("uri", ""))
                    norm_title = self.current_track.get("title", "").strip().lower()
                    for idx, t in enumerate(tracks):
                        tid = self._extract_id(t.get("uri", ""))
                        if (curr_id and tid == curr_id) or (t.get("title", "").strip().lower() == norm_title):
                            c_idx = idx
                            break
                if c_idx is None:
                    c_idx = 0

            def is_incomplete(t):
                no_title = t.get("title") in ("Трек", "", None, "Track")
                no_artist = not t.get("artist") or t.get("artist").strip().casefold() == "spotify"
                return no_title or no_artist

            any_updated = False

            # 1. High-priority window: visible in queue (15 before, 45 after)
            start_i = max(0, c_idx - 15)
            end_i = min(n_tracks, c_idx + 45)

            missing_priority = []
            for i in range(start_i, end_i):
                t = tracks[i]
                tid = self._extract_id(t.get("uri", ""))
                cached = self._lookup_track_meta(tid)
                if cached:
                    if t.get("title") in ("Трек", "", None, "Track") and cached.get("title"):
                        t["title"] = cached.get("title")
                    c_art = (cached.get("artist") or "").strip()
                    if (not t.get("artist") or t.get("artist") == "Spotify") and c_art and c_art != "Spotify":
                        t["artist"] = c_art
                    if not t.get("album") and cached.get("album"):
                        t["album"] = cached.get("album")
                if tid and is_incomplete(t):
                    missing_priority.append((i, tid))

            if missing_priority:
                for i, tid in missing_priority:
                    if getattr(self, "_rate_limited_until", 0) > time.time():
                        break
                    meta = self._fetch_track_info(tid)
                    if meta and i < len(tracks):
                        if meta.get("title"):
                            tracks[i]["title"] = meta["title"]
                        if meta.get("artist") and meta["artist"] != "Spotify":
                            tracks[i]["artist"] = meta["artist"]
                        if meta.get("album"):
                            tracks[i]["album"] = meta["album"]
                        any_updated = True
                    time.sleep(0.35)

            # 2. Gentle background pass for the rest of the playlist
            remaining_missing = []
            for i in range(n_tracks):
                if i < start_i or i >= end_i:
                    t = tracks[i]
                    tid = self._extract_id(t.get("uri", ""))
                    cached = self._lookup_track_meta(tid)
                    if cached:
                        if t.get("title") in ("Трек", "", None, "Track") and cached.get("title"):
                            t["title"] = cached.get("title")
                        c_art = (cached.get("artist") or "").strip()
                        if (not t.get("artist") or t.get("artist") == "Spotify") and c_art and c_art != "Spotify":
                            t["artist"] = c_art
                        if not t.get("album") and cached.get("album"):
                            t["album"] = cached.get("album")
                    if tid and is_incomplete(t):
                        remaining_missing.append((i, tid))

            if remaining_missing:
                for i, tid in remaining_missing:
                    if getattr(self, "_rate_limited_until", 0) > time.time():
                        break
                    meta = self._fetch_track_info(tid)
                    if meta and i < len(tracks):
                        if meta.get("title"):
                            tracks[i]["title"] = meta["title"]
                        if meta.get("artist") and meta["artist"] != "Spotify":
                            tracks[i]["artist"] = meta["artist"]
                        if meta.get("album"):
                            tracks[i]["album"] = meta["album"]
                        any_updated = True
                    time.sleep(0.7)

            if any_updated:
                self.save_cache()
                GLib.idle_add(lambda: self.notify(order_changed=False))

        t = threading.Thread(target=resolver, daemon=True)
        t.start()

    def _sync_context_async(self, track_id, title, artist, album):
        if self._fetching:
            return
        self._fetching = True

        def worker():
            try:
                active_ctx = None
                if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
                    active_ctx = self.context_uri
                else:
                    active_ctx = self._detect_active_context_uri(track_id)

                if active_ctx and active_ctx.startswith("spotify:playlist:"):
                    playlist_id = active_ctx.split(":")[-1]

                    # 1. Fetch playlist name via LevelDB, cache, or embed
                    pl_name = self._get_playlist_name_from_ldb(playlist_id)
                    embed_tracks = []
                    if active_ctx in self.context_cache:
                        c_name, embed_tracks = self.context_cache[active_ctx]
                        if not pl_name:
                            pl_name = c_name
                    if not pl_name or not embed_tracks:
                        e_name, e_tracks = self._fetch_embed_tracks(active_ctx)
                        if not pl_name:
                            pl_name = e_name
                        if not embed_tracks:
                            embed_tracks = e_tracks
                        if pl_name:
                            self.context_cache[active_ctx] = (pl_name, embed_tracks)

                    # 2. Extract full playlist tracks in real sorted order from local LevelDB
                    tracks = self._extract_playlist_from_ldb(playlist_id)

                    if not tracks and embed_tracks:
                        tracks = embed_tracks
                        for idx, t in enumerate(tracks):
                            t["track_num"] = idx + 1

                    # Apply any cached titles/artists to tracks
                    for t in tracks:
                        tid = t.get("tid")
                        if tid in self.track_meta_cache:
                            cached = self.track_meta_cache[tid]
                            if t.get("title") in ("Трек", "", None):
                                t["title"] = cached.get("title", t.get("title"))
                                t["artist"] = cached.get("artist", t.get("artist"))

                    # 3. Locate current track in the playlist
                    curr_idx = -1
                    curr_title = title.strip().lower()
                    for idx, t in enumerate(tracks):
                        tid = self._extract_id(t.get("uri", ""))
                        if (track_id and tid == track_id) or (t.get("title", "").strip().lower() == curr_title):
                            curr_idx = idx
                            t["title"] = title
                            t["artist"] = artist
                            break

                    with self._lock:
                        if pl_name and is_valid_name(pl_name):
                            self.context_name = pl_name
                        self.context_uri = active_ctx
                        self.all_context_tracks = tracks
                        if curr_idx >= 0:
                            self.current_track = {
                                "title": title,
                                "artist": artist,
                                "uri": f"spotify:track:{track_id}" if track_id else "",
                                "track_num": curr_idx + 1
                            }

                    GLib.idle_add(self.notify)

                    # 4. Resolve metadata for visible tracks and then whole playlist in background
                    if curr_idx >= 0:
                        self._resolve_missing_tracks_async(curr_idx)
                    return

                elif active_ctx and active_ctx.startswith("spotify:album:"):
                    name, tracks = self._fetch_embed_tracks(active_ctx)
                    for idx, t in enumerate(tracks):
                        t["track_num"] = idx + 1
                    # Ensure current track is actually in this album
                    if track_id and tracks and not any(t.get("tid") == track_id for t in tracks):
                        return
                    with self._lock:
                        cand = name or album
                        if is_valid_name(cand):
                            self.context_name = cand
                        self.context_uri = active_ctx
                        if tracks:
                            self.all_context_tracks = tracks
                    GLib.idle_add(self.notify)
                    return

                # Fallback only when NO context is active at all
                if track_id:
                    # Check if track belongs to an active user playlist first!
                    active_pl = self._find_active_playlist_for_track(track_id)
                    if active_pl:
                        pid = active_pl.split(":")[-1]
                        pl_name = self._get_playlist_name_from_ldb(pid)
                        fresh_tracks = self._extract_playlist_from_ldb(pid)
                        if fresh_tracks:
                            with self._lock:
                                if is_valid_name(pl_name):
                                    self.context_name = pl_name
                                self.context_uri = active_pl
                                self.all_context_tracks = fresh_tracks
                            GLib.idle_add(self.notify)
                            return

                    album_name, album_tracks, alb_id = self._fetch_album_for_track(track_id)
                    for idx, t in enumerate(album_tracks):
                        t["track_num"] = idx + 1
                    with self._lock:
                        cand = album_name or album
                        if is_valid_name(cand):
                            self.context_name = cand
                        if alb_id:
                            self.context_uri = f"spotify:album:{alb_id}"
                        if album_tracks:
                            self.all_context_tracks = album_tracks
                    GLib.idle_add(self.notify)

            except Exception as e:
                print(f"Error syncing Spotify context: {e}")
            finally:
                self._fetching = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _find_active_playlist_for_track(self, track_id):
        """Finds the authentic user playlist containing track_id.
        Hierarchy:
        1. Current active playlist (self.context_uri) if it contains track_id.
        2. Authoritative Spotify playback record (rp#ctx / yl#rpp) in primary.ldb if it contains track_id.
        3. User-owned playlists in primary.ldb (where pl#members contains user ID) ranked by track count descending.
        4. Recently active playlists in Spotify Browser Local Storage, ONLY if user-owned.
        """
        if not track_id:
            return None

        # 1. Current active playlist
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            cur_pid = self.context_uri.split(":")[-1]
            if any(t.get("tid") == track_id or self._extract_id(t.get("uri", "")) == track_id for t in self.all_context_tracks):
                return self.context_uri
            cur_tracks = self._extract_playlist_from_ldb(cur_pid)
            if cur_tracks and any(t.get("tid") == track_id or self._extract_id(t.get("uri", "")) == track_id for t in cur_tracks):
                return self.context_uri

        # 2. Authoritative active playlist from Spotify LevelDB rp#ctx / yl#rpp
        active_sp_pid = self._get_active_spotify_playlist()
        if active_sp_pid:
            sp_tracks = self._extract_playlist_from_ldb(active_sp_pid)
            if sp_tracks and any(t.get("tid") == track_id or self._extract_id(t.get("uri", "")) == track_id for t in sp_tracks):
                return f"spotify:playlist:{active_sp_pid}"

        # 3. User-owned playlists in primary.ldb
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        all_files = sorted([f for f in files if f.endswith(".log")], key=os.path.getmtime, reverse=True) + \
                    sorted([f for f in files if f.endswith(".ldb")], key=os.path.getmtime, reverse=True)
        checked_user_pids = set()
        user_candidates = []

        for fpath in all_files:
            try:
                d = read_ldb_clean(fpath)
                for m in re.finditer(rb"1!pl#slc#\x27spotify:playlist:([a-zA-Z0-9]{22})#", d):
                    pid = m.group(1).decode()
                    if pid in checked_user_pids:
                        continue
                    checked_user_pids.add(pid)
                    if not self._is_user_owned_playlist(pid):
                        continue
                    name = self._get_playlist_name_from_ldb(pid)
                    if not name or not is_valid_name(name):
                        continue
                    tracks = self._extract_playlist_from_ldb(pid)
                    if tracks and any(t.get("tid") == track_id or self._extract_id(t.get("uri", "")) == track_id for t in tracks):
                        user_candidates.append((pid, len(tracks), name))
            except Exception:
                pass

        if user_candidates:
            user_candidates.sort(key=lambda x: x[1], reverse=True)
            return f"spotify:playlist:{user_candidates[0][0]}"

        # 4. Spotify Browser Local Storage (active/viewed playlists) - ONLY if user-owned
        browser_dir = os.path.expanduser("~/.cache/spotify/Browser/Local Storage/leveldb")
        if os.path.exists(browser_dir):
            logs = sorted(
                glob.glob(os.path.join(browser_dir, "*.log")) + glob.glob(os.path.join(browser_dir, "*.ldb")),
                key=os.path.getmtime,
                reverse=True
            )
            checked_browser = set()
            for fpath in logs:
                try:
                    with open(fpath, "rb") as fp:
                        d = fp.read()
                    pids = re.findall(rb"spotify:playlist:([a-zA-Z0-9]{22})", d)
                    for p in reversed(pids):
                        pid = p.decode()
                        if pid in checked_browser or pid.startswith("37i9dQZF"):
                            continue
                        checked_browser.add(pid)
                        if self._is_user_owned_playlist(pid):
                            tracks = self._extract_playlist_from_ldb(pid)
                            if tracks and any(t.get("tid") == track_id or self._extract_id(t.get("uri", "")) == track_id for t in tracks):
                                return f"spotify:playlist:{pid}"
                except Exception:
                    pass

        return None

    def _detect_active_context_uri(self, current_track_id=None):
        """Inspects Spotify's local context_player_state_restore and LevelDB,
        finding the authentic active playback context."""
        tid = current_track_id
        if not tid and self.current_track:
            tid = self._extract_id(self.current_track.get("uri"))

        # If current track belongs to an active user playlist, that playlist is ALWAYS the authentic context!
        if tid:
            pl = self._find_active_playlist_for_track(tid)
            if pl:
                return pl

        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/context_player_state_restore"))
        if files:
            try:
                with open(files[0], "rb") as fp:
                    c = fp.read()

                # 1. Targeted check: find the active context associated with the current track
                if tid:
                    tid_bytes = tid.encode()
                    pos = c.rfind(tid_bytes)
                    if pos != -1:
                        chunk = c[max(0, pos - 150):min(len(c), pos + 150)]
                        m_container = re.search(rb"(?:context_uri|entity_uri)[^\x00]*?(spotify:(?:collection:tracks|(?:playlist|album):[a-zA-Z0-9:]+))", chunk)
                        if m_container:
                            cand = m_container.group(1).decode()
                            if cand.startswith("spotify:playlist:") or cand.startswith("spotify:collection:"):
                                return cand
                        m_track = re.search(rb"(?:context_uri|entity_uri)[^\x00]*?(spotify:track:[a-zA-Z0-9:]+)", chunk)
                        if m_track:
                            return m_track.group(1).decode()

                # 2. General scan: find all context_uri and entity_uri entries by timestamp
                best_container_ts = 0
                best_container_ctx = None
                best_track_ts = 0
                best_track_ctx = None
                pos = 0
                while pos < len(c):
                    p = c.find(b"\x08", pos)
                    if p == -1:
                        break
                    try:
                        val, _ = decode_varint(c, p + 1)
                        if 1700000000000 <= val <= 1850000000000:
                            chunk = c[p:min(len(c), p + 300)]
                            m = re.search(rb"(?:context_uri|entity_uri)[^\x00]*?(spotify:(?:collection:tracks|(?:playlist|album|artist|track):[a-zA-Z0-9:]+))", chunk)
                            if m:
                                cand = m.group(1).decode()
                                if cand.startswith("spotify:track:"):
                                    if val > best_track_ts:
                                        best_track_ts = val
                                        best_track_ctx = cand
                                else:
                                    if val > best_container_ts:
                                        best_container_ts = val
                                        best_container_ctx = cand
                    except Exception:
                        pass
                    pos = p + 1

                # If container is a playlist, verify if current track belongs to it
                if best_container_ctx and best_container_ctx.startswith("spotify:playlist:"):
                    pid = best_container_ctx.split(":")[-1]
                    if tid:
                        pl_tracks = self._extract_playlist_from_ldb(pid)
                        if pl_tracks and any(t.get("tid") == tid for t in pl_tracks):
                            return best_container_ctx
                    else:
                        return best_container_ctx

                # If container is an album and contains current track, or is fresher than standalone track:
                if best_container_ctx and best_container_ctx.startswith("spotify:album:"):
                    if best_container_ts >= best_track_ts:
                        return best_container_ctx

                if best_track_ctx:
                    return best_track_ctx
                if best_container_ctx:
                    return best_container_ctx

            except Exception as e:
                print(f"Error reading context_player_state_restore: {e}")

        # Fallback to playlist search only if playlist actually contains track
        if tid:
            pl = self._find_active_playlist_for_track(tid)
            if pl:
                return pl

        return None

    def _fetch_embed_tracks(self, context_uri):
        if context_uri in self.context_cache:
            return self.context_cache[context_uri]
        try:
            parts = context_uri.split(":")
            ctype, cid = parts[1], parts[2]
            url = f"https://open.spotify.com/embed/{ctype}/{cid}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=5) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r"<script id=\"__NEXT_DATA__\"[^>]*>(.*?)</script>", html)
                if m:
                    data = json.loads(m.group(1))
                    entity = data.get("props", {}).get("pageProps", {}).get("state", {}).get("data", {}).get("entity", {})
                    name = entity.get("name", "")
                    track_list = entity.get("trackList", [])
                    res = []
                    for idx, t in enumerate(track_list):
                        tid = self._extract_id(t.get("uri", ""))
                        title_str = (t.get("title") or "").strip()
                        artist_str = (t.get("subtitle") or "").strip()
                        if artist_str.casefold() == "spotify":
                            artist_str = ""
                        if tid and title_str:
                            self.track_meta_cache[tid] = {
                                "title": title_str,
                                "artist": artist_str,
                                "uri": t.get("uri", "")
                            }
                        res.append({
                            "track_num": idx + 1,
                            "title": title_str,
                            "artist": artist_str,
                            "uri": t.get("uri", ""),
                            "tid": tid
                        })
                    self.context_cache[context_uri] = (name, res)
                    return name, res
        except Exception as e:
            print(f"Failed to fetch embed tracks for {context_uri}: {e}")
        return "", []

    def _fetch_album_for_track(self, track_id):
        if not track_id:
            return "", [], ""
        cache_key = f"track_album:{track_id}"
        if cache_key in self.context_cache:
            return self.context_cache[cache_key]
        try:
            url = f"https://open.spotify.com/track/{track_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r"/album/([a-zA-Z0-9]{22})", html)
                if m:
                    album_id = m.group(1)
                    name, tracks = self._fetch_embed_tracks(f"spotify:album:{album_id}")
                    if tracks:
                        self.context_cache[cache_key] = (name, tracks, album_id)
                        for t in tracks:
                            tid = t.get("tid")
                            if tid:
                                self.context_cache[f"track_album:{tid}"] = (name, tracks, album_id)
                        return name, tracks, album_id
        except Exception as e:
            print(f"Failed to fetch album for track {track_id}: {e}")
        return "", [], ""
