"""MCP Display companion service -- see README.md.

    python -m mcp_display_companion                      # run service
    python -m mcp_display_companion --check              # validate configuration and exit
    python -m mcp_display_companion --print-view         # print published HTML
    python -m mcp_display_companion --set-token TOKEN    # save token outside repo
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from . import config as config_module
from . import view
from .__init__ import __version__
from .config import ConfigError
from .instance import InstanceLock
from .runtime import Runtime, setup_log

DEFAULT_CONFIG = "config.json"


def _mask(token: str) -> str:
    if len(token) <= 8:
        return "*" * len(token)
    return f"{token[:4]}...{token[-4:]} ({len(token)} characters)"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="mcp_display_companion",
        description="Executes MCP Display panel touch actions on the computer.",
    )
    parser.add_argument("-c", "--config", default=DEFAULT_CONFIG,
                        help=f"configuration file (default: {DEFAULT_CONFIG})")
    parser.add_argument("--check", action="store_true",
                        help="validate configuration, print summary, and exit")
    parser.add_argument("--print-view", action="store_true",
                        help="print view HTML and exit (for inspection)")
    parser.add_argument("--set-token", metavar="TOKEN",
                        help="save token to secrets file (outside repository) and exit")
    parser.add_argument("--version", action="version", version=f"mcp-display-companion {__version__}")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    if args.set_token:
        try:
            dest = config_module.save_token(args.set_token)
        except ConfigError as exc:
            print(f"could not save token: {exc}", file=sys.stderr)
            return 2
        print(f"token saved to {dest} (user-only permissions)")
        return 0

    try:
        cfg = config_module.load(args.config)
    except ConfigError as exc:
        print(f"invalid configuration: {exc}", file=sys.stderr)
        return 2

    if args.print_view:
        from .slideshow import SlideDeck

        has_slides = SlideDeck(cfg.slides).has_images() if cfg.slides.enabled else False
        print(view.render(cfg, has_slides=has_slides))
        return 0

    if args.check:
        print(f"config           : {args.config}")
        print(f"panel            : {cfg.panel.url}")
        print(f"token            : {_mask(cfg.panel.token)}")
        print(f"cursor           : {cfg.panel.cursor_file}")
        print(f"log              : {cfg.loop.log_file}")
        print(f"view template    : {cfg.view.template}")
        print(f"weather          : {'enabled - ' + cfg.weather.label if cfg.weather.enabled else 'disabled'}")
        print(f"media            : {'enabled' if cfg.media.enabled else 'disabled'}")
        if cfg.slides.enabled:
            from .slideshow import SlideDeck

            deck = SlideDeck(cfg.slides)
            if deck.has_images():
                print(f"slideshow        : {len(deck._files)} image(s) in {cfg.slides.folder}")
            else:
                print(f"slideshow        : empty folder, page will not appear ({cfg.slides.folder})")
        else:
            print("slideshow        : disabled")
        print(f"brightness       : {cfg.display.brightness if cfg.display.enabled else 'unmanaged'}")
        print(f"actions ({len(cfg.actions)}):")
        for action in cfg.actions:
            badge = " [featured]" if action.featured else ""
            print(f"  - {action.summary()}{badge}")
        return 0

    log = setup_log(cfg.loop.log_file)

    # Single instance: two companions connected to same panel conflict over View.
    lock_path = Path(os.environ.get("PANEL_COMPANION_LOCK") or (cfg.base_dir / "companion.lock"))
    lock = InstanceLock(lock_path)
    if not lock.acquire():
        owner = lock.owner_pid()
        log(
            f"another companion is already running with this configuration "
            f"(pid {owner}, lock {lock_path}); exiting without publishing"
        )
        return 0
    try:
        Runtime(cfg, log=log).run()
    finally:
        lock.release()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
