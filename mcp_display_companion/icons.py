"""SVG icons for the panel.

Each name becomes a `<symbol id="i-<name>">` in the published HTML; the View uses
`<use href="#i-<name>">`. For an icon not listed here, use `icon_svg` in the
action, with the inner content of a `viewBox="0 0 24 24"`:

    {"id": "pc_light", "label": "Light", "type": "command",
     "cmd": ["..."], "icon_svg": "<path d=\"M12 2 2 22h20z\"/>"}
"""

ICONS: dict[str, str] = {
    # media (filled)
    "play": '<path d="M8 5.5v13a1 1 0 0 0 1.5.9l10.4-6.5a1 1 0 0 0 0-1.8L9.5 4.6A1 1 0 0 0 8 5.5z"/>',
    "pause": '<path d="M7 4h3.5a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1H7a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1zM13.5 4H17a1 1 0 0 1 1 1v14a1 1 0 0 1-1 1h-3.5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1z"/>',
    "playpause": '<path d="M8 5.5v13a1 1 0 0 0 1.5.9l10.4-6.5a1 1 0 0 0 0-1.8L9.5 4.6A1 1 0 0 0 8 5.5z"/>',
    "prev": '<path d="M5 5h2v14H5zM20 6.1v11.8a.8.8 0 0 1-1.25.65L10.2 12.65a.8.8 0 0 1 0-1.3l8.55-5.9A.8.8 0 0 1 20 6.1z"/>',
    "next": '<path d="M17 5h2v14h-2zM4 6.1v11.8a.8.8 0 0 0 1.25.65l8.55-5.9a.8.8 0 0 0 0-1.3L5.25 5.45A.8.8 0 0 0 4 6.1z"/>',
    "stop": '<path d="M6 6h12v12H6z"/>',
    # volume
    "volume_up": '<path d="M3 9v6h4l5 5V4L7 9zM15 11h6v2h-6zM17 9h2v6h-2z"/>',
    "volume_down": '<path d="M3 9v6h4l5 5V4L7 9zM15 11h6v2h-6z"/>',
    "mute": '<path d="M3 9v6h4l5 5V4L7 9zM15.3 9.3l1.4-1.4 2.3 2.3 2.3-2.3 1.4 1.4-2.3 2.3 2.3 2.3-1.4 1.4-2.3-2.3-2.3 2.3-1.4-1.4 2.3-2.3z"/>',
    # apps and folders
    "spotify": '<path d="M12 2C6.48 2 2 6.48 2 12s4.48 10 10 10 10-4.48 10-10S17.52 2 12 2zm4.58 14.42c-.18.3-.56.4-.86.22-2.36-1.44-5.33-1.77-8.83-.97-.34.08-.68-.13-.76-.47-.08-.34.13-.68.47-.76 3.83-.87 7.12-.5 9.76 1.12.3.18.4.56.22.86zm1.22-2.72c-.23.37-.72.49-1.09.26-2.7-1.66-6.82-2.14-10.02-1.17-.42.13-.86-.11-.99-.53-.13-.42.11-.86.53-.99 3.65-1.11 8.21-.57 11.31 1.34.37.23.49.72.26 1.09zm.11-2.83C14.67 9.1 9.34 8.92 6.25 9.86c-.5.15-1.03-.14-1.18-.64-.15-.5.14-1.03.64-1.18 3.56-1.08 9.47-.87 13.25 1.37.45.27.6.86.33 1.31-.27.45-.86.6-1.38.35z"/>',
    "youtube": '<path d="M21.58 7.19a2.76 2.76 0 0 0-1.95-1.96C17.9 4.75 12 4.75 12 4.75s-5.9 0-7.63.48c-.96.26-1.7.99-1.95 1.96C2 8.92 2 12 2 12s0 3.08.42 4.81c.25.97.99 1.7 1.95 1.96 1.73.48 7.63.48 7.63.48s5.9 0 7.63-.48c.96-.26 1.7-.99 1.95-1.96.42-1.73.42-4.81.42-4.81s0-3.08-.42-4.81zM9.75 15.02V8.98l5.5 3.02-5.5 3.02z"/>',
    "chrome": '<path d="M12 2a10 10 0 0 1 8.66 5h-8.65a3 3 0 0 0-2.83 2H4.6A10 10 0 0 1 12 2zm-9.6 7h6.4a3 3 0 0 0 4.35 3.3L10 22A10 10 0 0 1 2.4 9zm9.6 8.5 4.4-7.6c.3.5.5 1.1.5 1.7a4.9 4.9 0 0 1-.1 1A10 10 0 0 1 12 22h.5l-.5-2.5z"/>',
    "folder": '<path d="M10 4H4c-1.1 0-1.99.9-1.99 2L2 18c0 1.1.9 2 2 2h16c1.1 0 2-.9 2-2V8c0-1.1-.9-2-2-2h-8l-2-2z"/>',
    "download": '<path d="M19.35 10.04C18.67 6.59 15.64 4 12 4 9.11 4 6.6 5.64 5.35 8.04 2.34 8.36 0 10.91 0 14c0 3.31 2.69 6 6 6h13c2.76 0 5-2.24 5-5 0-2.64-2.05-4.78-4.65-4.96zM17 13l-5 5-5-5h3V9h4v4h3z"/>',
    "city": '<path d="M12 7V3H2v18h20V7H12zM6 19H4v-2h2v2zm0-4H4v-2h2v2zm0-4H4V9h2v2zm0-4H4V5h2v2zm4 12H8v-2h2v2zm0-4H8v-2h2v2zm0-4H8V9h2v2zm0-4H8V5h2v2zm10 12h-8v-2h2v-2h-2v-2h2v-2h-2V9h8v10zm-2-8h-2v2h2v-2zm0 4h-2v2h2v-2z"/>',
    "home": '<path d="M12 3 2 12h3v9h6v-6h2v6h6v-9h3z"/>',
    "terminal": '<path d="M2 4h20v16H2zm3.4 4.3 3 3-3 3 1.4 1.4 4.4-4.4-4.4-4.4zm6.6 7.2h6v2h-6z"/>',
    "power": '<path d="M13 3h-2v10h2zM17.7 6.3l-1.4 1.4a7 7 0 1 1-8.6 0L6.3 6.3a9 9 0 1 0 11.4 0z"/>',
    "music": '<path d="M12 3v10.6A4 4 0 1 0 14 17V7h5V3z"/>',
    "dot": '<circle cx="12" cy="12" r="6"/>',
}

# icons that should not toggle with playback state
PLAY_STATE_ICONS = {"play", "pause", "playpause"}


def symbol_def(name: str, svg_body: str | None = None) -> str:
    """Returns the `<symbol>` markup for the icon name, using the catalog or `svg_body`."""
    body = svg_body or ICONS.get(name) or ICONS["dot"]
    return f'<symbol id="i-{name}" viewBox="0 0 24 24">{body}</symbol>'
