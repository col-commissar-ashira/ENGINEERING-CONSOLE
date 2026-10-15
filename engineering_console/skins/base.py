"""A skin is a complete visual language for the whole application: stylesheet, fonts,
header/banner styling, window background and (optionally) gauge painting.

A theme selects a skin with its "skin" key; colours still come from the theme, so every
colour in the Appearance dialog works with every skin. Skins never touch application
state or monitoring logic, and the app replaces ALL skin styling whenever the theme
changes - so one skin cannot leak into another. To add a new look: subclass Skin,
register it in skins/__init__.py, and give a theme  skin="yourname".
"""


class Skin:
    name = "base"

    def font_stack(self):               # families used by custom-painted text
        raise NotImplementedError

    def stylesheet(self, T, dp):        # application-wide QSS (T = theme, dp = density props)
        raise NotImplementedError

    def title_style(self, T, dp):
        raise NotImplementedError

    def subtitle_style(self, T, dp):
        raise NotImplementedError

    def clock_style(self, T, dp):
        raise NotImplementedError

    def status_style(self, alert, T, dp):
        raise NotImplementedError

    def banner_style(self, bucket, dp):
        raise NotImplementedError

    def paint_background(self, panel, p, win):
        raise NotImplementedError

    def paints_gauges(self):
        return False

    def paint_gauge(self, gauge, p):    # only called when paints_gauges() is True
        raise NotImplementedError

    def prepare(self, win):             # create any assets the skin needs (idempotent)
        pass
