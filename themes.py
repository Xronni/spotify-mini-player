"""
Visual themes for Spotify Mini Player.

style.css paints every colour through named `@define-color smp_*` tokens. A theme is just a
palette of those tokens; ThemeManager keeps one extra CssProvider (above the app's own) that
redefines them, so switching a theme never touches the stylesheet itself.

The accent (Spotify green) is defined only in style.css and is never overridden by a theme.
"""
import colorsys

from gi.repository import Gtk, Gdk, GdkPixbuf, GLib, Adw

THEME_LIGHT = "light"
THEME_NATIVE = "native"
THEME_BLACK = "black"
THEME_COVER = "cover"
THEMES = (THEME_LIGHT, THEME_NATIVE, THEME_BLACK, THEME_COVER)
DEFAULT_THEME = THEME_NATIVE

# Token -> (r, g, b, a) with r/g/b in 0..255 and a in 0..1
PALETTES = {
    THEME_NATIVE: {
        "card_bg": (18, 18, 22, 0.85),
        "card_bg2": (18, 18, 22, 0.85),
        "card_border": (255, 255, 255, 0.12),
        "fg": (255, 255, 255, 1.0),
        "fg_strong": (244, 244, 245, 1.0),
        "fg_soft": (212, 212, 216, 1.0),
        "fg_dim": (161, 161, 170, 1.0),
        "fg_muted": (113, 113, 122, 1.0),
        "icon": (142, 142, 147, 1.0),
        "knob": (255, 255, 255, 1.0),
        "eq_paused": (82, 82, 91, 1.0),
        "cover_bg": (0, 0, 0, 0.3),
        "veil": (12, 12, 16, 0.62),
    },
    THEME_LIGHT: {
        "card_bg": (250, 250, 251, 0.95),
        "card_bg2": (244, 244, 246, 0.95),
        "card_border": (0, 0, 0, 0.10),
        "fg": (24, 24, 27, 1.0),
        "fg_strong": (39, 39, 42, 1.0),
        "fg_soft": (63, 63, 70, 1.0),
        "fg_dim": (82, 82, 91, 1.0),
        "fg_muted": (113, 113, 122, 1.0),
        "icon": (82, 82, 91, 1.0),
        "knob": (255, 255, 255, 1.0),
        "eq_paused": (161, 161, 170, 1.0),
        "cover_bg": (0, 0, 0, 0.06),
        "veil": (250, 250, 251, 0.66),
    },
    THEME_BLACK: {
        "card_bg": (6, 6, 6, 1.0),
        "card_bg2": (6, 6, 6, 1.0),
        "card_border": (255, 255, 255, 0.06),
        "fg": (245, 245, 245, 1.0),
        "fg_strong": (230, 230, 230, 1.0),
        "fg_soft": (200, 200, 200, 1.0),
        "fg_dim": (150, 150, 150, 1.0),
        "fg_muted": (102, 102, 102, 1.0),
        "icon": (130, 130, 130, 1.0),
        "knob": (240, 240, 240, 1.0),
        "eq_paused": (60, 60, 60, 1.0),
        "cover_bg": (14, 14, 14, 1.0),
        "veil": (0, 0, 0, 0.7),
    },
}

TRANSITION_MS = 420
TRANSITION_STEP_MS = 30


def _hls_color(h, l, s, a=1.0):
    r, g, b = colorsys.hls_to_rgb(h % 1.0, max(0.0, min(1.0, l)), max(0.0, min(1.0, s)))
    return (r * 255.0, g * 255.0, b * 255.0, a)


def extract_cover_colors(path, sample=32):
    """Returns ((h, l, s) primary, (h, l, s) secondary) for the cover at `path`, or None."""
    try:
        pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(path, sample, sample, False)
    except Exception:
        return None

    n_ch = pixbuf.get_n_channels()
    stride = pixbuf.get_rowstride()
    width, height = pixbuf.get_width(), pixbuf.get_height()
    pixels = pixbuf.get_pixels()
    has_alpha = pixbuf.get_has_alpha()

    buckets = {}  # hue bucket -> [weight, r, g, b]
    total = [0.0, 0.0, 0.0, 0.0]
    for y in range(height):
        row = y * stride
        for x in range(width):
            i = row + x * n_ch
            if has_alpha and pixels[i + 3] < 128:
                continue
            r, g, b = pixels[i] / 255.0, pixels[i + 1] / 255.0, pixels[i + 2] / 255.0
            total[0] += 1
            total[1] += r
            total[2] += g
            total[3] += b
            h, l, s = colorsys.rgb_to_hls(r, g, b)
            # Near-black, near-white and grey pixels say little about the cover's colour.
            if l < 0.08 or l > 0.94 or s < 0.18:
                continue
            weight = (0.25 + s) * (1.0 - abs(l - 0.5))
            key = int(h * 18) % 18
            bucket = buckets.setdefault(key, [0.0, 0.0, 0.0, 0.0])
            bucket[0] += weight
            bucket[1] += r * weight
            bucket[2] += g * weight
            bucket[3] += b * weight

    if total[0] == 0:
        return None

    def bucket_hls(bucket):
        w = bucket[0]
        return colorsys.rgb_to_hls(bucket[1] / w, bucket[2] / w, bucket[3] / w)

    ranked = sorted(buckets.items(), key=lambda kv: kv[1][0], reverse=True)
    # Colourful pixels must cover a meaningful part of the image, otherwise treat it as monochrome.
    if not ranked or ranked[0][1][0] < total[0] * 0.04:
        avg = colorsys.rgb_to_hls(total[1] / total[0], total[2] / total[0], total[3] / total[0])
        return avg, avg

    primary = bucket_hls(ranked[0][1])
    secondary = primary
    for key, bucket in ranked[1:]:
        dist = min(abs(key - ranked[0][0]), 18 - abs(key - ranked[0][0]))
        if dist >= 2 and bucket[0] >= ranked[0][1][0] * 0.18:
            secondary = bucket_hls(bucket)
            break
    return primary, secondary


def cover_palette(colors):
    """Builds a dark palette tinted by the cover's colours (the accent stays Spotify green)."""
    if not colors:
        return dict(PALETTES[THEME_NATIVE])
    (h1, _l1, s1), (h2, _l2, s2) = colors
    # Keep the tint rich but never neon; monochrome covers stay close to neutral.
    s1 = min(s1, 0.62)
    s2 = min(s2, 0.62)
    st = min(s1, 0.35)  # text tint
    return {
        "card_bg": _hls_color(h1, 0.15, s1, 0.92),
        "card_bg2": _hls_color(h2, 0.08, s2, 0.92),
        "card_border": _hls_color(h1, 0.55, s1, 0.28),
        "fg": (255, 255, 255, 1.0),
        "fg_strong": _hls_color(h1, 0.95, st),
        "fg_soft": _hls_color(h1, 0.86, st),
        "fg_dim": _hls_color(h1, 0.74, st),
        "fg_muted": _hls_color(h1, 0.60, st),
        "icon": _hls_color(h1, 0.70, st),
        "knob": (255, 255, 255, 1.0),
        "eq_paused": _hls_color(h1, 0.38, st),
        "cover_bg": _hls_color(h1, 0.06, s1, 0.5),
        "veil": _hls_color(h1, 0.06, s1, 0.62),
    }


def palette_to_css(palette):
    lines = []
    for name, (r, g, b, a) in palette.items():
        lines.append(f"@define-color smp_{name} rgba({int(round(r))}, {int(round(g))}, {int(round(b))}, {a:.3f});")
    return "\n".join(lines) + "\n"


def _lerp_palette(src, dst, t):
    out = {}
    for name, target in dst.items():
        start = src.get(name, target)
        out[name] = tuple(s + (d - s) * t for s, d in zip(start, target))
    return out


class ThemeManager:
    def __init__(self, theme=DEFAULT_THEME):
        self.theme = theme if theme in THEMES else DEFAULT_THEME
        self._provider = Gtk.CssProvider()
        Gtk.StyleContext.add_provider_for_display(
            Gdk.Display.get_default(),
            self._provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION + 1,
        )
        self._cover_path = None
        self._cover_cache = {}
        self._current = None
        self._anim_from = None
        self._anim_to = None
        self._anim_start = 0
        self._anim_id = None
        self._apply_scheme()
        self._set_target(self._target_palette(), animate=False)

    # --- public API -------------------------------------------------------------------------

    def set_theme(self, theme):
        if theme not in THEMES or theme == self.theme:
            return False
        self.theme = theme
        self._apply_scheme()
        self._set_target(self._target_palette(), animate=True)
        return True

    def set_cover(self, path):
        """Called on every metadata update with the current cover path (or None)."""
        if path == self._cover_path:
            return
        self._cover_path = path
        if self.theme == THEME_COVER:
            self._set_target(self._target_palette(), animate=True)

    # --- internals --------------------------------------------------------------------------

    def _apply_scheme(self):
        scheme = Adw.ColorScheme.FORCE_LIGHT if self.theme == THEME_LIGHT else Adw.ColorScheme.FORCE_DARK
        Adw.StyleManager.get_default().set_color_scheme(scheme)

    def _target_palette(self):
        if self.theme != THEME_COVER:
            return PALETTES[self.theme]
        path = self._cover_path
        if not path:
            return cover_palette(None)
        if path not in self._cover_cache:
            if len(self._cover_cache) > 64:
                self._cover_cache.clear()
            self._cover_cache[path] = cover_palette(extract_cover_colors(path))
        return self._cover_cache[path]

    def _load(self, palette):
        css = palette_to_css(palette)
        if hasattr(self._provider, "load_from_string"):
            self._provider.load_from_string(css)
        else:
            self._provider.load_from_data(css, -1)
        self._current = palette

    def _set_target(self, palette, animate):
        if self._anim_id:
            GLib.source_remove(self._anim_id)
            self._anim_id = None
        if not animate or self._current is None:
            self._load(palette)
            return
        self._anim_from = self._current
        self._anim_to = palette
        self._anim_start = GLib.get_monotonic_time()
        self._anim_id = GLib.timeout_add(TRANSITION_STEP_MS, self._anim_step)

    def _anim_step(self):
        elapsed = (GLib.get_monotonic_time() - self._anim_start) / 1000.0
        t = min(1.0, elapsed / TRANSITION_MS)
        ease = t * t * (3.0 - 2.0 * t)
        self._load(_lerp_palette(self._anim_from, self._anim_to, ease))
        if t >= 1.0:
            self._anim_id = None
            return False
        return True
