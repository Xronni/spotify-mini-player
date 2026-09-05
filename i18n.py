"""
Internationalization (i18n) module for Spotify Mini Player.
Automatically detects Spotify's UI language and provides localized UI strings.
"""
import os
import glob
import re
import json

MESSAGES = {
    "en": {
        "queue_title": "Play Queue",
        "queue_prefix": "Queue: ",
        "now_playing": "NOW PLAYING",
        "playing": "PLAYING",
        "next_in_queue": "NEXT IN QUEUE",
        "recently_played": "RECENTLY PLAYED",
        "queue_empty": "Queue finished",
        "collapse_queue": "Collapse queue",
        "queue_tooltip": "Play Queue",
        "pin_on": "Unpin (Always visible)",
        "pin_off": "Pin on screen (Always visible)",
        "open_spotify": "Open Spotify window",
        "hide_player": "Hide mini player",
        "minimize_player": "Minimize mini player",
        "close_player": "Close mini player",
        "prev_track": "Previous track",
        "next_track": "Next track",
        "play_pause": "Play / Pause",
        "play_again": "Play again",
        "no_track": "No track",
        "track_default": "Track",
        "volume": "Volume: {vol}%",
        "offline_title": "Spotify is not running",
        "launch_spotify": "Launch Spotify",
        "mute_unmute": "Mute / Unmute",
        "spotify_volume": "Spotify Volume",
        "focus_spotify": "Click to focus Spotify",
        "shuffle_enabled": "Shuffle: On",
        "shuffle_disabled": "Shuffle: Off",
        "repeat_none": "Repeat: Off",
        "repeat_playlist": "Repeat: Playlist",
        "repeat_track": "Repeat: Track",
        "updating": "Updating queue...",
    },
    "ru": {
        "queue_title": "Очередь воспроизведения",
        "queue_prefix": "Очередь: ",
        "now_playing": "СЕЙЧАС ИГРАЕТ",
        "playing": "ИГРАЕТ",
        "next_in_queue": "СЛЕДУЮЩИЕ В ОЧЕРЕДИ",
        "recently_played": "РАНЕЕ ИГРАЛИ",
        "queue_empty": "Очередь завершена",
        "collapse_queue": "Свернуть очередь",
        "queue_tooltip": "Очередь треков",
        "pin_on": "Открепить от верха",
        "pin_off": "Закрепить поверх всех окон",
        "open_spotify": "Открыть окно Spotify",
        "hide_player": "Скрыть мини-плеер",
        "minimize_player": "Свернуть мини-плеер",
        "close_player": "Закрыть мини-плеер",
        "prev_track": "Предыдущий трек",
        "next_track": "Следующий трек",
        "play_pause": "Воспроизведение / Пауза",
        "play_again": "Слушать заново",
        "no_track": "Нет трека",
        "track_default": "Трек",
        "volume": "Громкость: {vol}%",
        "offline_title": "Spotify не запущен",
        "launch_spotify": "Запустить Spotify",
        "mute_unmute": "Отключить / Включить звук",
        "spotify_volume": "Громкость Spotify",
        "focus_spotify": "Нажмите, чтобы открыть Spotify",
        "shuffle_enabled": "Перемешивание: Вкл",
        "shuffle_disabled": "Перемешивание: Выкл",
        "repeat_none": "Повтор: Выкл",
        "repeat_playlist": "Повтор: Плейлист",
        "repeat_track": "Повтор: Трек",
        "updating": "Обновление очереди...",
    },
    "de": {
        "queue_title": "Warteschlange",
        "queue_prefix": "Warteschlange: ",
        "now_playing": "AKTUELLE WIEDERGABE",
        "playing": "SPIELT",
        "next_in_queue": "ALS NÄCHSTES IN DER WARTESCHLANGE",
        "recently_played": "ZULETZT GESPIELT",
        "queue_empty": "Warteschlange beendet",
        "collapse_queue": "Warteschlange einklappen",
        "queue_tooltip": "Warteschlange",
        "pin_on": "Oben lösen",
        "pin_off": "Immer im Vordergrund anheften",
        "open_spotify": "Spotify-Fenster öffnen",
        "hide_player": "Mini-Player ausblenden",
        "minimize_player": "Mini-Player minimieren",
        "close_player": "Mini-Player schließen",
        "prev_track": "Vorheriger Titel",
        "next_track": "Nächster Titel",
        "play_pause": "Wiedergabe / Pause",
        "play_again": "Erneut abspielen",
        "no_track": "Kein Titel",
        "track_default": "Titel",
        "volume": "Lautstärke: {vol}%",
        "offline_title": "Spotify läuft nicht",
        "launch_spotify": "Spotify starten",
        "mute_unmute": "Stummschalten / Ton an",
        "spotify_volume": "Spotify Lautstärke",
        "focus_spotify": "Klicken, um Spotify zu fokussieren",
        "shuffle_enabled": "Zufallswiedergabe: Ein",
        "shuffle_disabled": "Zufallswiedergabe: Aus",
        "repeat_none": "Wiederholen: Aus",
        "repeat_playlist": "Wiederholen: Playlist",
        "repeat_track": "Wiederholen: Titel",
        "updating": "Warteschlange wird aktualisiert...",
    },
    "es": {
        "queue_title": "Cola de reproducción",
        "queue_prefix": "Cola: ",
        "now_playing": "EN REPRODUCCIÓN",
        "playing": "REPRODUCIENDO",
        "next_in_queue": "SIGUIENTE EN COLA",
        "recently_played": "REPRODUCIDO RECIENTEMENTE",
        "queue_empty": "Cola finalizada",
        "collapse_queue": "Ocultar cola",
        "queue_tooltip": "Cola de reproducción",
        "pin_on": "Desfijar de la pantalla",
        "pin_off": "Fijar en pantalla (Siempre visible)",
        "open_spotify": "Abrir ventana de Spotify",
        "hide_player": "Ocultar reproductor",
        "minimize_player": "Minimizar mini-reproductor",
        "close_player": "Cerrar mini-reproductor",
        "prev_track": "Pista anterior",
        "next_track": "Siguiente pista",
        "play_pause": "Reproducir / Pausa",
        "play_again": "Escuchar de nuevo",
        "no_track": "Sin pista",
        "track_default": "Pista",
        "volume": "Volumen: {vol}%",
        "offline_title": "Spotify no se está ejecutando",
        "launch_spotify": "Abrir Spotify",
        "mute_unmute": "Silenciar / Activar sonido",
        "spotify_volume": "Volumen de Spotify",
        "focus_spotify": "Haz clic para abrir Spotify",
        "shuffle_enabled": "Aleatorio: Activado",
        "shuffle_disabled": "Aleatorio: Desactivado",
        "repeat_none": "Repetir: Desactivado",
        "repeat_playlist": "Repetir: Lista",
        "repeat_track": "Repetir: Canción",
        "updating": "Actualizando cola...",
    },
    "fr": {
        "queue_title": "File d'attente",
        "queue_prefix": "File d'attente : ",
        "now_playing": "TITRE EN COURS",
        "playing": "LECTURE",
        "next_in_queue": "À SUIVRE",
        "recently_played": "RÉCEMMENT ÉCOUTÉS",
        "queue_empty": "File d'attente terminée",
        "collapse_queue": "Réduire la file d'attente",
        "queue_tooltip": "File d'attente",
        "pin_on": "Détacher du premier plan",
        "pin_off": "Épingler au premier plan",
        "open_spotify": "Ouvrir Spotify",
        "hide_player": "Masquer le mini-lecteur",
        "minimize_player": "Réduire le mini-lecteur",
        "close_player": "Fermer le mini-lecteur",
        "prev_track": "Titre précédent",
        "next_track": "Titre suivant",
        "play_pause": "Lecture / Pause",
        "play_again": "Réécouter",
        "no_track": "Aucun titre",
        "track_default": "Titre",
        "volume": "Volume : {vol}%",
        "offline_title": "Spotify n'est pas lancé",
        "launch_spotify": "Lancer Spotify",
        "mute_unmute": "Couper / Activer le son",
        "spotify_volume": "Volume Spotify",
        "focus_spotify": "Cliquer pour afficher Spotify",
        "shuffle_enabled": "Aléatoire: Activé",
        "shuffle_disabled": "Aléatoire: Désactivé",
        "repeat_none": "Répéter: Désactivé",
        "repeat_playlist": "Répéter: Playlist",
        "repeat_track": "Répéter: Titre",
        "updating": "Mise à jour de la file...",
    }
}

_current_lang = None

def detect_spotify_language():
    """Detects the interface language set in Spotify application.
    Prioritizes user preferences where Spotify stores user language selections.
    """
    # 1. From Spotify user prefs file (where Spotify persists language="xx")
    user_prefs = glob.glob(os.path.expanduser("~/.config/spotify/**/prefs"), recursive=True)
    for up in sorted(user_prefs, key=os.path.getmtime, reverse=True):
        try:
            with open(up, "r", encoding="utf-8", errors="ignore") as f:
                for line in f:
                    if line.startswith("language=") or line.startswith("app.language="):
                        val = line.split("=", 1)[1].strip().strip("\"'").lower()
                        base = val.split("-")[0].split("_")[0]
                        if base in MESSAGES:
                            return base
        except Exception:
            pass

    # 2. From Spotify Browser Preferences (CEF Local Storage)
    pref_files = glob.glob(os.path.expanduser("~/.cache/spotify/**/Preferences"), recursive=True)
    for pf in sorted(pref_files, key=os.path.getmtime, reverse=True):
        try:
            with open(pf, "r", encoding="utf-8") as f:
                d = json.load(f)
            intl = d.get("intl", {})
            sel = intl.get("selected_languages") or intl.get("accept_languages")
            if sel:
                lang = sel.split(",")[0].strip().lower()
                base = lang.split("-")[0].split("_")[0]
                if base in MESSAGES:
                    return base
        except Exception:
            pass

    # 3. From running Spotify process args (--lang=xx-YY, only if explicitly non-default)
    for p in glob.glob("/proc/[0-9]*/cmdline"):
        try:
            with open(p, "rb") as f:
                c = f.read()
            if b"spotify" in c:
                m = re.search(rb"--lang=([a-zA-Z0-9_-]+)", c)
                if m:
                    lang = m.group(1).decode("utf-8", errors="ignore").lower()
                    base = lang.split("-")[0].split("_")[0]
                    if base in MESSAGES and base != "en":
                        return base
        except Exception:
            continue

    # 4. Fallback to system locale
    sys_lang = (os.environ.get("LANG", "") or os.environ.get("LC_MESSAGES", "")).lower()
    if sys_lang:
        base = sys_lang.split(".")[0].split("-")[0].split("_")[0]
        if base in MESSAGES:
            return base

    return "en"

def get_current_language():
    global _current_lang
    if not _current_lang:
        _current_lang = detect_spotify_language()
    return _current_lang

def set_language(lang_code):
    global _current_lang
    if lang_code in MESSAGES:
        _current_lang = lang_code

def t(key, **kwargs):
    """Translates a message key according to the active Spotify language."""
    lang = get_current_language()
    bundle = MESSAGES.get(lang) or MESSAGES.get("en", {})
    text = bundle.get(key) or MESSAGES["en"].get(key, key)
    if kwargs:
        text = text.format(**kwargs)
    return text
