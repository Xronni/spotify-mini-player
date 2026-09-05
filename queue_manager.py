import os
import re
import glob
import json
import time
import urllib.request
import threading
from concurrent.futures import ThreadPoolExecutor
from gi.repository import GLib

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
        self._lock = threading.Lock()
        self.load_cache()

    def load_cache(self):
        cache_file = os.path.join(CACHE_DIR, "queue_cache.json")
        if os.path.exists(cache_file):
            try:
                with open(cache_file, "r", encoding="utf-8") as f:
                    data = json.load(f)
                    c_name = data.get("context_name", "")
                    self.context_name = c_name if is_valid_name(c_name) else ""
                    self.context_uri = data.get("context_uri", "")
                    self.all_context_tracks = data.get("all_context_tracks", [])
                    self.session_history = data.get("session_history", [])
                    self.context_cache = data.get("context_cache", {})
                    self.track_meta_cache = data.get("track_meta_cache", {})
                    self._last_sort_state = data.get("sort_state", None)
                    if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
                        pid = self.context_uri.split(":")[-1]
                        active_sort = self._get_playlist_sort_state(pid)
                        if active_sort != self._last_sort_state:
                            self._last_sort_state = active_sort
                            fresh_tracks = self._extract_playlist_from_ldb(pid)
                            if fresh_tracks:
                                self.all_context_tracks = fresh_tracks
            except Exception as e:
                print(f"Error loading queue cache: {e}")

    def save_cache(self):
        try:
            os.makedirs(CACHE_DIR, exist_ok=True)
            cache_file = os.path.join(CACHE_DIR, "queue_cache.json")
            data = {
                "context_name": self.context_name if is_valid_name(self.context_name) else "",
                "context_uri": self.context_uri,
                "all_context_tracks": self.all_context_tracks,
                "session_history": self.session_history,
                "context_cache": self.context_cache,
                "track_meta_cache": self.track_meta_cache,
                "sort_state": self._last_sort_state
            }
            with open(cache_file, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f"Error saving queue cache: {e}")

    def notify(self):
        self.save_cache()
        if self.on_queue_changed_cb:
            self.on_queue_changed_cb()

    def _find_track_idx(self, uri, title=""):
        if not self.all_context_tracks:
            return -1
        curr_id = self._extract_id(uri)
        if curr_id:
            for idx, t in enumerate(self.all_context_tracks):
                if self._extract_id(t.get("uri", "")) == curr_id:
                    return idx
        if title:
            norm_title = title.strip().lower()
            if norm_title and norm_title not in ("трек", "нет трека", "track", "no track"):
                for idx, t in enumerate(self.all_context_tracks):
                    t_title = t.get("title", "").strip().lower()
                    if t_title and t_title not in ("трек", "track", "") and t_title == norm_title:
                        return idx
        return -1

    def get_past_tracks(self, limit=40):
        """Returns ONLY the tracks that come BEFORE the current track in this playlist, in strict 1..curr_idx-1 order."""
        if self.all_context_tracks and self.current_track:
            curr_idx = self._find_track_idx(
                self.current_track.get("uri", ""),
                self.current_track.get("title", "")
            )
            if curr_idx > 0:
                self.last_valid_idx = curr_idx
                start_i = max(0, curr_idx - limit) if limit else 0
                return self.all_context_tracks[start_i:curr_idx]
            elif curr_idx == 0:
                self.last_valid_idx = 0
                return []
            else:
                # If current_track is temporarily off-playlist, preserve past tracks using last_valid_idx
                l_idx = getattr(self, "last_valid_idx", 0)
                if l_idx > 0 and l_idx < len(self.all_context_tracks):
                    start_i = max(0, l_idx - limit) if limit else 0
                    return self.all_context_tracks[start_i:l_idx]
        return []

    def get_upcoming_tracks(self, limit=60):
        """Returns upcoming tracks in the current playlist in order."""
        if self.all_context_tracks and self.current_track:
            curr_idx = self._find_track_idx(
                self.current_track.get("uri", ""),
                self.current_track.get("title", "")
            )
            if curr_idx >= 0:
                self.last_valid_idx = curr_idx
                if curr_idx + 1 < len(self.all_context_tracks):
                    end_i = curr_idx + 1 + limit if limit else len(self.all_context_tracks)
                    return self.all_context_tracks[curr_idx + 1:end_i]
            else:
                # If current_track is temporarily off-playlist, preserve upcoming tracks using last_valid_idx
                l_idx = getattr(self, "last_valid_idx", 0)
                if l_idx + 1 < len(self.all_context_tracks):
                    end_i = l_idx + 1 + limit if limit else len(self.all_context_tracks)
                    return self.all_context_tracks[l_idx + 1:end_i]
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
                with open(fpath, "rb") as fp:
                    d = fp.read()
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
        if is_valid_name(self.context_name):
            return self.context_name

        # 1. From context_uri if playlist
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

        # 2. Try detecting active context from restore state or LevelDB
        detected = self._detect_active_context_uri()
        if detected:
            if detected.startswith("spotify:playlist:"):
                pid = detected.split(":")[-1]
                ldb_name = self._get_playlist_name_from_ldb(pid)
                if is_valid_name(ldb_name):
                    self.context_name = ldb_name
                    self.context_uri = detected
                    return self.context_name
            elif detected.startswith("spotify:album:") and current_album:
                if is_valid_name(current_album):
                    self.context_name = current_album
                    self.context_uri = detected
                    return self.context_name

        # 3. Fallback to current album if playing an album
        if current_album and is_valid_name(current_album):
            return current_album

        return ""

    def check_for_updates(self):
        """Checks if active playlist or sort order was modified in Spotify LevelDB and refreshes if needed."""
        if not self.context_uri or not self.context_uri.startswith("spotify:playlist:"):
            return
        pid = self.context_uri.split(":")[-1]
        log_files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*.log"))
        browser_files = glob.glob(os.path.expanduser("~/.cache/spotify/Browser/Local Storage/leveldb/*.log"))

        needs_refresh = False
        try:
            if log_files:
                latest_ldb_mtime = max(os.path.getmtime(f) for f in log_files)
                if latest_ldb_mtime > self._last_ldb_mtime:
                    self._last_ldb_mtime = latest_ldb_mtime
                    needs_refresh = True

            if browser_files:
                latest_browser_mtime = max(os.path.getmtime(f) for f in browser_files)
                if latest_browser_mtime > self._last_browser_ldb_mtime:
                    self._last_browser_ldb_mtime = latest_browser_mtime
                    cur_sort = self._get_playlist_sort_state(pid)
                    if cur_sort != self._last_sort_state:
                        print(f"[QueueManager] Sort state changed for {pid}: {self._last_sort_state} -> {cur_sort}")
                        self._last_sort_state = cur_sort
                        needs_refresh = True
            else:
                cur_sort = self._get_playlist_sort_state(pid)
                if cur_sort != self._last_sort_state:
                    print(f"[QueueManager] Sort state changed for {pid}: {self._last_sort_state} -> {cur_sort}")
                    self._last_sort_state = cur_sort
                    needs_refresh = True

            if needs_refresh:
                fresh_tracks = self._extract_playlist_from_ldb(pid)
                if fresh_tracks:
                    for t in fresh_tracks:
                        tid = t.get("tid")
                        if tid in self.track_meta_cache:
                            cached = self.track_meta_cache[tid]
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
                    self.notify()
        except Exception as e:
            print(f"Error checking for playlist updates: {e}")

    def _find_playlist_for_track(self, track_id):
        """Quickly scans local LevelDB to find which USER playlist slice contains track_id."""
        if not track_id:
            return None
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        log_files = [f for f in files if f.endswith(".log")]
        ldb_files = [f for f in files if f.endswith(".ldb")]
        all_files = sorted(log_files, key=os.path.getmtime, reverse=True) + \
                    sorted(ldb_files, key=os.path.getmtime, reverse=True)
        track_bytes = track_id.encode()
        for fpath in all_files:
            try:
                with open(fpath, "rb") as fp:
                    d = fp.read()
                if track_bytes not in d:
                    continue
                for m in re.finditer(rb"1!pl#slc#\x27spotify:playlist:([a-zA-Z0-9]{22})#", d):
                    pid = m.group(1).decode()
                    # Must have a valid playlist name (exclude empty/corrupt)
                    name = self._get_playlist_name_from_ldb(pid)
                    if not name or not is_valid_name(name):
                        continue
                    pos = m.start()
                    next_slice = d.find(b"1!pl#", pos + len(m.group(0)))
                    chunk = d[pos:next_slice] if next_slice != -1 else d[pos:pos + 400000]
                    if track_bytes in chunk:
                        return pid
            except Exception:
                pass
        return None

    def update_current_track(self, title, artist, uri, album=""):
        if not title:
            return

        # Normalize URI format
        if uri and uri.startswith("/com/spotify/track/"):
            uri = "spotify:track:" + uri.split("/")[-1]

        curr_id = self._extract_id(uri)
        if curr_id:
            self.track_meta_cache[curr_id] = {"title": title, "artist": artist, "uri": uri}

        old_track = self.current_track
        norm_title = title.strip().lower()

        # Check if song changed
        if old_track and old_track.get("title") and old_track.get("title", "").strip().lower() != norm_title:
            if not self.session_history or self.session_history[-1].get("title", "").strip().lower() != old_track["title"].strip().lower():
                self.session_history.append(dict(old_track))
                if len(self.session_history) > 40:
                    self.session_history.pop(0)

        # Check if sort order of active playlist changed
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            cur_pid = self.context_uri.split(":")[-1]
            active_sort = self._get_playlist_sort_state(cur_pid)
            if active_sort != self._last_sort_state:
                self._last_sort_state = active_sort
                fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
                if fresh_tracks:
                    with self._lock:
                        self.all_context_tracks = fresh_tracks

        # 1. Check if current track is already within our active context tracks
        found_idx = self._find_track_idx(uri, title)
        if found_idx >= 0:
            self.last_valid_idx = found_idx
            t = self.all_context_tracks[found_idx]
            if t.get("title") in ("Трек", "", None, "Track"):
                t["title"] = title
                t["artist"] = artist
            self.current_track = {
                "title": title,
                "artist": artist,
                "uri": uri,
                "track_num": found_idx + 1
            }
            if not is_valid_name(self.context_name):
                self.get_context_name(current_album=album)
            self.notify()
            self._resolve_missing_tracks_async(found_idx)
            return

        # 2. Track NOT found in currently loaded all_context_tracks!
        # Case A: Track might have been newly added to the CURRENT playlist (e.g. 17 nozhevyh)
        if self.context_uri and self.context_uri.startswith("spotify:playlist:"):
            cur_pid = self.context_uri.split(":")[-1]
            fresh_tracks = self._extract_playlist_from_ldb(cur_pid)
            fresh_idx = -1
            if fresh_tracks:
                for idx, t in enumerate(fresh_tracks):
                    if (curr_id and t["tid"] == curr_id) or (t.get("title", "").strip().lower() == norm_title):
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
                        "track_num": fresh_idx + 1
                    }
                    if not is_valid_name(self.context_name):
                        self.get_context_name(current_album=album)
                self.notify()
                self._resolve_missing_tracks_async(fresh_idx)
                return

        # Case B: User switched to another real USER playlist!
        detected_pid = self._find_playlist_for_track(curr_id) if curr_id else None
        if detected_pid:
            fresh_tracks = self._extract_playlist_from_ldb(detected_pid)
            fresh_idx = -1
            for idx, t in enumerate(fresh_tracks):
                if (curr_id and t["tid"] == curr_id) or (t.get("title", "").strip().lower() == norm_title):
                    fresh_idx = idx
                    t["title"] = title
                    t["artist"] = artist
                    break

            pl_ctx_uri = f"spotify:playlist:{detected_pid}"
            ldb_name = self._get_playlist_name_from_ldb(detected_pid)
            cached_name = ldb_name or self.context_cache.get(pl_ctx_uri, ("", []))[0]

            with self._lock:
                self.context_uri = pl_ctx_uri
                if is_valid_name(cached_name):
                    self.context_name = cached_name
                elif not is_valid_name(self.context_name):
                    self.context_name = album if is_valid_name(album) else ""
                self.all_context_tracks = fresh_tracks
                self.last_valid_idx = fresh_idx if fresh_idx >= 0 else 0
                self.current_track = {
                    "title": title,
                    "artist": artist,
                    "uri": uri,
                    "track_num": fresh_idx + 1 if fresh_idx >= 0 else 1
                }
            self.notify()

            self._sync_context_async(curr_id, title, artist, album)
            if fresh_idx >= 0:
                self._resolve_missing_tracks_async(fresh_idx)
            return

        # Case C: Context detected from Spotify context_player_state_restore
        detected_ctx = self._detect_active_context_uri()
        if detected_ctx:
            if detected_ctx.startswith("spotify:playlist:"):
                new_pid = detected_ctx.split(":")[-1]
                fresh_tracks = self._extract_playlist_from_ldb(new_pid)
                if fresh_tracks:
                    fresh_idx = -1
                    for idx, t in enumerate(fresh_tracks):
                        if (curr_id and t["tid"] == curr_id) or (t.get("title", "").strip().lower() == norm_title):
                            fresh_idx = idx
                            t["title"] = title
                            t["artist"] = artist
                            break
                    pl_name = self._get_playlist_name_from_ldb(new_pid)
                    with self._lock:
                        self.context_uri = detected_ctx
                        if is_valid_name(pl_name):
                            self.context_name = pl_name
                        self.all_context_tracks = fresh_tracks
                        self.last_valid_idx = fresh_idx if fresh_idx >= 0 else 0
                        self.current_track = {
                            "title": title,
                            "artist": artist,
                            "uri": uri,
                            "track_num": fresh_idx + 1 if fresh_idx >= 0 else 1
                        }
                    self.notify()
                    self._sync_context_async(curr_id, title, artist, album)
                    if fresh_idx >= 0:
                        self._resolve_missing_tracks_async(fresh_idx)
                    return
                else:
                    name, emb_tracks = self._fetch_embed_tracks(detected_ctx)
                    if emb_tracks:
                        fresh_idx = -1
                        for idx, t in enumerate(emb_tracks):
                            if (curr_id and t["tid"] == curr_id) or (t.get("title", "").strip().lower() == norm_title):
                                fresh_idx = idx
                                break
                        with self._lock:
                            self.context_uri = detected_ctx
                            if is_valid_name(name):
                                self.context_name = name
                            self.all_context_tracks = emb_tracks
                            self.last_valid_idx = fresh_idx if fresh_idx >= 0 else 0
                            self.current_track = {
                                "title": title,
                                "artist": artist,
                                "uri": uri,
                                "track_num": fresh_idx + 1 if fresh_idx >= 0 else 1
                            }
                        self.notify()
                        return
            elif detected_ctx.startswith("spotify:album:"):
                name, emb_tracks = self._fetch_embed_tracks(detected_ctx)
                if emb_tracks:
                    fresh_idx = -1
                    for idx, t in enumerate(emb_tracks):
                        if (curr_id and t["tid"] == curr_id) or (t.get("title", "").strip().lower() == norm_title):
                            fresh_idx = idx
                            break
                    with self._lock:
                        self.context_uri = detected_ctx
                        self.context_name = name or album or "Альбом"
                        self.all_context_tracks = emb_tracks
                        self.last_valid_idx = fresh_idx if fresh_idx >= 0 else 0
                        self.current_track = {
                            "title": title,
                            "artist": artist,
                            "uri": uri,
                            "track_num": fresh_idx + 1 if fresh_idx >= 0 else 1
                        }
                    self.notify()
                    return

        # Case D: Try fetching album tracks for track
        if curr_id and is_valid_name(album):
            name, emb_tracks = self._fetch_album_for_track(curr_id)
            if emb_tracks:
                fresh_idx = -1
                for idx, t in enumerate(emb_tracks):
                    if (curr_id and t["tid"] == curr_id) or (t.get("title", "").strip().lower() == norm_title):
                        fresh_idx = idx
                        break
                with self._lock:
                    self.context_uri = f"spotify:album:{curr_id}"
                    self.context_name = name or album
                    self.all_context_tracks = emb_tracks
                    self.last_valid_idx = fresh_idx if fresh_idx >= 0 else 0
                    self.current_track = {
                        "title": title,
                        "artist": artist,
                        "uri": uri,
                        "track_num": fresh_idx + 1 if fresh_idx >= 0 else 1
                    }
                self.notify()
                return

        # Case E: Standalone single track fallback
        with self._lock:
            self.current_track = {"title": title, "artist": artist, "uri": uri, "track_num": 1}
            if not is_valid_name(self.context_name) and is_valid_name(album):
                self.context_name = album
        self.notify()
        self._sync_context_async(curr_id, title, artist, album)

    def _extract_id(self, uri):
        if not uri:
            return ""
        return uri.split(":")[-1].split("/")[-1].split("?")[0]

    def _get_playlist_sort_state(self, playlist_id):
        """Reads user's active sorting preference for playlist_id from Spotify's Browser Local Storage."""
        browser_dir = os.path.expanduser("~/.cache/spotify/Browser/Local Storage/leveldb")
        if not os.path.exists(browser_dir):
            return None

        files = sorted(
            glob.glob(os.path.join(browser_dir, "*.log")) + glob.glob(os.path.join(browser_dir, "*.ldb")),
            key=os.path.getmtime,
            reverse=True
        )
        if not files:
            return None

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
                        if isinstance(data, dict):
                            if target in data:
                                val = data[target]
                                if isinstance(val, dict) and val.get("field"):
                                    return val
                                return None
                            if data == {}:
                                return None
                    except Exception:
                        pass

        return None

    def _extract_playlist_from_ldb(self, playlist_id):
        """Extracts all tracks directly from Spotify's LevelDB slice in real authentic sequence."""
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/primary.ldb/*"))
        target = ("1!pl#slc#\x27spotify:playlist:" + playlist_id + "#").encode("utf-8")

        found_chunk = None
        for fpath in sorted(files, key=os.path.getmtime, reverse=True):
            if fpath.endswith(".log") or fpath.endswith(".ldb"):
                with open(fpath, "rb") as fp:
                    d = fp.read()
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
            if e["tid"] in self.track_meta_cache:
                cached = self.track_meta_cache[e["tid"]]
                e["title"] = cached.get("title", "Трек")
                e["artist"] = cached.get("artist", "Spotify")
                e["album"] = cached.get("album", "")
                e["duration"] = cached.get("duration", 0)
            else:
                e["title"] = "Трек"
                e["artist"] = "Spotify"
                e["album"] = ""
                e["duration"] = 0

        # Apply active Spotify sort configuration for all columns
        sort_state = self._get_playlist_sort_state(playlist_id)
        if sort_state and isinstance(sort_state, dict):
            field = str(sort_state.get("field", "")).upper()
            order = str(sort_state.get("order", "ASC")).upper()
            reverse = (order == "DESC")
            if field == "ADDED_AT":
                entries.sort(key=lambda x: x.get("added_at", 0), reverse=reverse)
            elif field in ("TITLE", "NAME"):
                entries.sort(key=lambda x: (x.get("title") or "").strip().casefold(), reverse=reverse)
            elif field == "ARTIST":
                entries.sort(key=lambda x: (x.get("artist") or "").strip().casefold(), reverse=reverse)
            elif field == "ALBUM":
                entries.sort(key=lambda x: (x.get("album") or "").strip().casefold(), reverse=reverse)
            elif field in ("DURATION", "TIME"):
                entries.sort(key=lambda x: x.get("duration", 0), reverse=reverse)
            elif field in ("ADDED_BY", "USER"):
                entries.sort(key=lambda x: (x.get("added_by") or "").strip().casefold(), reverse=reverse)

        # Set 1-based playlist index
        for idx, e in enumerate(entries):
            e["track_num"] = idx + 1

        return entries

    def _fetch_track_info(self, track_id):
        if not track_id:
            return None
        if track_id in self.track_meta_cache:
            return self.track_meta_cache[track_id]
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
                    a_name = ", ".join([a["name"] for a in entity.get("artists", [])]) if entity.get("artists") else "Spotify"
                    album_obj = entity.get("album") or {}
                    alb_name = album_obj.get("name", "") if isinstance(album_obj, dict) else ""
                    dur = entity.get("duration", 0)
                    if t_name:
                        info = {"title": t_name, "artist": a_name, "uri": f"spotify:track:{track_id}", "album": alb_name, "duration": dur}
                        self.track_meta_cache[track_id] = info
                        return info
        except Exception:
            pass

        # Fallback to oembed (never rate limited)
        try:
            o_url = f"https://open.spotify.com/oembed?url=https://open.spotify.com/track/{track_id}"
            req = urllib.request.Request(o_url, headers={"User-Agent": "Mozilla/5.0"})
            with urllib.request.urlopen(req, timeout=3) as resp:
                d = json.loads(resp.read().decode())
                t_name = d.get("title")
                if t_name:
                    info = {"title": t_name, "artist": "Spotify", "uri": f"spotify:track:{track_id}"}
                    self.track_meta_cache[track_id] = info
                    return info
        except Exception:
            pass
        return None

    def _resolve_missing_tracks_async(self, curr_idx=None):
        if not self.all_context_tracks:
            return

        def resolver():
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

            # 1. High-priority window: visible in queue (15 before, 45 after)
            start_i = max(0, c_idx - 15)
            end_i = min(n_tracks, c_idx + 45)

            missing_priority = []
            for i in range(start_i, end_i):
                t = tracks[i]
                tid = self._extract_id(t.get("uri", ""))
                if tid in self.track_meta_cache:
                    cached = self.track_meta_cache[tid]
                    if t.get("title") in ("Трек", "", None):
                        t["title"] = cached.get("title", t.get("title"))
                        t["artist"] = cached.get("artist", t.get("artist"))
                elif tid and t.get("title") in ("Трек", "", None):
                    missing_priority.append((i, tid))

            if missing_priority:
                def _fetch_one(pair):
                    i, tid = pair
                    meta = self._fetch_track_info(tid)
                    return i, meta

                with ThreadPoolExecutor(max_workers=8) as executor:
                    results = list(executor.map(_fetch_one, missing_priority))

                updated = False
                for i, meta in results:
                    if meta and i < len(tracks):
                        tracks[i]["title"] = meta.get("title", tracks[i].get("title"))
                        tracks[i]["artist"] = meta.get("artist", tracks[i].get("artist"))
                        updated = True

                if updated:
                    self.save_cache()
                    GLib.idle_add(self.notify)

            # 2. Background pass for the rest of the playlist
            remaining_missing = []
            for i in range(n_tracks):
                if i < start_i or i >= end_i:
                    t = tracks[i]
                    tid = self._extract_id(t.get("uri", ""))
                    if tid in self.track_meta_cache:
                        cached = self.track_meta_cache[tid]
                        if t.get("title") in ("Трек", "", None):
                            t["title"] = cached.get("title", t.get("title"))
                            t["artist"] = cached.get("artist", t.get("artist"))
                    elif tid and t.get("title") in ("Трек", "", None):
                        remaining_missing.append((i, tid))

            if remaining_missing:
                for batch_start in range(0, len(remaining_missing), 10):
                    batch = remaining_missing[batch_start:batch_start + 10]
                    with ThreadPoolExecutor(max_workers=6) as executor:
                        batch_res = list(executor.map(lambda p: (p[0], self._fetch_track_info(p[1])), batch))
                    batch_updated = False
                    for i, meta in batch_res:
                        if meta and i < len(tracks):
                            tracks[i]["title"] = meta.get("title", tracks[i].get("title"))
                            tracks[i]["artist"] = meta.get("artist", tracks[i].get("artist"))
                            batch_updated = True
                    if batch_updated:
                        self.save_cache()
                    time.sleep(0.15)

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
                    active_ctx = self._detect_active_context_uri()

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
                    album_name, album_tracks = self._fetch_album_for_track(track_id)
                    for idx, t in enumerate(album_tracks):
                        t["track_num"] = idx + 1
                    with self._lock:
                        cand = album_name or album
                        if is_valid_name(cand):
                            self.context_name = cand
                        if album_tracks:
                            self.all_context_tracks = album_tracks
                    GLib.idle_add(self.notify)

            except Exception as e:
                print(f"Error syncing Spotify context: {e}")
            finally:
                self._fetching = False

        t = threading.Thread(target=worker, daemon=True)
        t.start()

    def _detect_active_context_uri(self):
        """Inspects Spotify's local context_player_state_restore by timestamp,
        finding the most recently active context."""
        files = glob.glob(os.path.expanduser("~/.cache/spotify/Users/*-user/context_player_state_restore"))
        if files:
            try:
                with open(files[0], "rb") as fp:
                    c = fp.read()
                best_ts = 0
                best_ctx = None
                pos = 0
                while pos < len(c):
                    p = c.find(b"\x08", pos)
                    if p == -1:
                        break
                    try:
                        val, _ = decode_varint(c, p + 1)
                        if 1700000000000 <= val <= 1850000000000:
                            chunk = c[p:min(len(c), p + 400)]
                            m = re.search(rb"context_uri[^\x00]*?(spotify:(?:playlist|album|collection:tracks|artist):[a-zA-Z0-9:]+)", chunk)
                            if m and val > best_ts:
                                cand = m.group(1).decode()
                                best_ts = val
                                best_ctx = cand
                    except Exception:
                        pass
                    pos = p + 1
                if best_ctx:
                    return best_ctx
            except Exception as e:
                print(f"Error reading context_player_state_restore: {e}")

        for f in sorted(glob.glob(os.path.expanduser("~/.cache/spotify/Browser/Local Storage/leveldb/*.log")),
                        key=os.path.getmtime, reverse=True):
            try:
                with open(f, "rb") as fp:
                    c = fp.read()
                playlists = re.findall(rb"spotify:playlist:([a-zA-Z0-9]{22})", c)
                for p in reversed(playlists):
                    pid = p.decode()
                    return f"spotify:playlist:{pid}"
            except Exception:
                pass
        return None

    def _fetch_embed_tracks(self, context_uri):
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
                        title_str = t.get("title", "")
                        artist_str = t.get("subtitle", "Spotify")
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
                    return name, res
        except Exception as e:
            print(f"Failed to fetch embed tracks for {context_uri}: {e}")
        return "", []

    def _fetch_album_for_track(self, track_id):
        try:
            url = f"https://open.spotify.com/track/{track_id}"
            req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64)"})
            with urllib.request.urlopen(req, timeout=4) as resp:
                html = resp.read().decode("utf-8", errors="ignore")
                m = re.search(r"/album/([a-zA-Z0-9]{22})", html)
                if m:
                    album_id = m.group(1)
                    return self._fetch_embed_tracks(f"spotify:album:{album_id}")
        except Exception as e:
            print(f"Failed to fetch album for track {track_id}: {e}")
        return "", []
