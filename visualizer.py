import math
from gi.repository import Gtk, GLib

class VisualizerWidget(Gtk.Box):
    def __init__(self, num_bars=5):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=2)
        self.num_bars = num_bars
        self.bars = []
        self.is_playing = False
        self.t = 0.0
        self.tick_id = None

        self.set_valign(Gtk.Align.CENTER)
        self.set_halign(Gtk.Align.CENTER)
        self.add_css_class("eq-container")

        # Frequencies and phases for natural fluid wave motion
        self.freqs = [2.6, 4.2, 5.5, 3.4, 6.2]
        self.phases = [0.0, 1.3, 2.7, 4.1, 0.8]
        self.current_heights = [3.0] * self.num_bars

        # Create vertical bar boxes
        for i in range(self.num_bars):
            bar = Gtk.Box()
            bar.set_valign(Gtk.Align.END)
            bar.add_css_class("eq-bar")
            bar.set_size_request(3, 3)
            self.append(bar)
            self.bars.append(bar)

        self.tick_id = self.add_tick_callback(self._on_tick)

    def set_playing(self, playing: bool):
        self.is_playing = playing
        for bar in self.bars:
            if playing:
                bar.remove_css_class("paused")
            else:
                bar.add_css_class("paused")

    def start(self):
        self.set_playing(True)

    def stop(self):
        self.set_playing(False)


    def _on_tick(self, widget, frame_clock):
        if self.is_playing:
            self.t += 0.055
            for i, bar in enumerate(self.bars):
                sin_val = math.sin(self.t * self.freqs[i % len(self.freqs)] + self.phases[i % len(self.phases)])
                target = 3.5 + (0.5 + 0.5 * sin_val) * 11.5
                self.current_heights[i] += (target - self.current_heights[i]) * 0.32
                h = max(3, min(16, int(round(self.current_heights[i]))))
                bar.set_size_request(3, h)
        else:
            # Smoothly settle to resting height (3px)
            needs_update = False
            for i, bar in enumerate(self.bars):
                if abs(self.current_heights[i] - 3.0) > 0.1:
                    self.current_heights[i] += (3.0 - self.current_heights[i]) * 0.25
                    h = max(3, int(round(self.current_heights[i])))
                    bar.set_size_request(3, h)
                    needs_update = True
                elif self.current_heights[i] != 3.0:
                    self.current_heights[i] = 3.0
                    bar.set_size_request(3, 3)

        return GLib.SOURCE_CONTINUE
