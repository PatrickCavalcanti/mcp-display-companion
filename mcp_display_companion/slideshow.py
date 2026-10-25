"""Slideshow page: looping images from a folder.

The panel cannot access the PC network or filesystem directly, so this module
reads the folder and delivers images: cropped to display aspect ratio, compressed
as JPEG, and sent as `data:` URLs via `update_view` (256 KB limit per call, and
10 calls per 10s -- hence one image per interval, not the entire folder at once).

Rules to avoid a frozen display:

- unreadable files are skipped without halting the loop;
- the folder is periodically rescanned so newly added photos appear without
  restarting the service;
- if `pillow` is missing (or on systems where it is not installed), the folder
  is skipped with a single log warning -- the rest of the panel keeps running.
"""

from __future__ import annotations

import base64
import io
import random
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .config import SlidesSection

EXTENSIONS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".gif"}
# margin for base64 (33%) and data URL header within the 256 KB limit
BASE64_LIMIT = 240_000


def _encode_pil(
    path: Path, width: int, height: int, quality: int
) -> str | None:
    """Crops to display aspect ratio and returns a JPEG `data:` URL."""
    try:
        from PIL import Image, ImageOps
    except ImportError:
        return None
    try:
        with Image.open(path) as image:
            # for JPEG, `draft` decodes at lower resolution directly: this keeps
            # 6000px photo preparation fast enough to avoid CPU spikes
            try:
                image.draft("RGB", (width * 2, height * 2))
            except Exception:  # noqa: BLE001 - formats without draft support
                pass
            image = ImageOps.exif_transpose(image)
            if image.mode not in ("RGB", "L"):
                image = image.convert("RGB")
            crop = ImageOps.fit(image, (width, height), method=Image.LANCZOS)
            for attempt in (quality, max(40, quality - 15), max(30, quality - 30)):
                buf = io.BytesIO()
                crop.save(buf, format="JPEG", quality=attempt)
                data = base64.b64encode(buf.getvalue()).decode("ascii")
                if len(data) <= BASE64_LIMIT:
                    return "data:image/jpeg;base64," + data
            # still too large: skip and let next image try
            return None
    except Exception:  # noqa: BLE001 - corrupt file, unsupported format, permissions
        return None


class SlideDeck:
    """Picks the next image from the folder at the configured cadence."""

    def __init__(
        self,
        section: SlidesSection,
        log: Callable[[str], None] = lambda m: None,
        encode: Callable[[Path, int, int, int], str | None] | None = None,
        shuffle: Callable[[list[Path]], None] | None = None,
        async_mode: bool = True,
        # backward compatibility aliases for kwargs
        sortear: Callable[[list[Path]], None] | None = None,
        assincrono: bool | None = None,
    ) -> None:
        self.section = section
        self.log = log
        self._encode = encode or _encode_pil
        sorter = shuffle if shuffle is not None else sortear
        self._shuffle = sorter if sorter is not None else random.shuffle
        self._async_mode = async_mode if assincrono is None else assincrono
        self._files: list[Path] = []
        self._index = 0
        self._last = 0.0
        self._last_scan = 0.0
        self._signature: tuple[str, ...] = ()
        self._no_pillow = False
        # image is prepared in a worker thread: compressing a photo takes hundreds
        # of milliseconds and must not block touch events
        self._ready: dict[str, Any] | None = None
        self._preparing = False
        self._lock = threading.Lock()



    # ------------------------------------------------------------------ #
    @property
    def folder(self) -> Path:
        return Path(self.section.folder)

    def has_images(self) -> bool:
        """Called when publishing: without images the slideshow page is omitted."""
        self._scan(time.time())
        return bool(self._files)

    def tem_imagens(self) -> bool:
        return self.has_images()

    def check_changed(self, now: float) -> bool:
        """Scans folder and reports whether presence of images changed.

        This allows the page to appear (or disappear) when photos are dropped
        in the folder, without restarting or pressing buttons.
        """
        if not self.section.enabled:
            return False
        before = bool(self._files)
        self._scan(now)
        return bool(self._files) != before

    def verificar(self, now: float) -> bool:
        return self.check_changed(now)

    def _scan(self, now: float) -> None:
        if now - self._last_scan < self.section.rescan_s and self._files:
            return
        self._last_scan = now
        folder = self.folder
        try:
            files = sorted(
                p for p in folder.iterdir()
                if p.is_file() and p.suffix.lower() in EXTENSIONS
            )
        except OSError as exc:
            if self._files:  # already had list: keep old list
                self.log(f"image folder unavailable ({exc}); continuing with previous list")
                return
            self._files = []
            self.log(f"image folder not found: {folder} ({exc})")
            return

        signature = tuple(str(p) for p in files)
        if signature == self._signature and self._files:
            return  # nothing changed: preserve current order/shuffle
        self._signature = signature
        if self.section.shuffle:
            self._shuffle(files)
        self._files = files
        if files:
            self.log(f"image folder: {len(files)} file(s) in {folder}")
        else:
            self.log(
                f"image folder empty: {folder} - slideshow page only "
                "appears when there is at least one image"
            )
        if self._index >= len(files):
            self._index = 0

    def due(self, now: float, force: bool = False) -> dict[str, Any] | None:
        if not self.section.enabled:
            return None
        if not force and now - self._last < self.section.interval_s:
            return None
        with self._lock:
            ready, self._ready = self._ready, None
        if ready is None:
            # still preparing first: request and return next step
            self._prepare_in_thread(now)
            return None
        self._last = now
        self._prepare_in_thread(now)  # prepare next one ahead of time
        return ready

    # ------------------------------------------------------------------ #
    def prepare_now(self, now: float | None = None) -> dict[str, Any] | None:
        """Prepares the next image and stages it for the next `due`."""
        slide = self._prepare(time.time() if now is None else now)
        with self._lock:
            self._ready = slide
        return slide

    def _prepare_in_thread(self, now: float) -> None:
        if not self._async_mode:
            self.prepare_now(now)
            return
        with self._lock:
            if self._preparing or self._ready is not None:
                return
            self._preparing = True

        def worker() -> None:
            try:
                slide = self._prepare(now)
                if slide:
                    with self._lock:
                        self._ready = slide
            finally:
                with self._lock:
                    self._preparing = False

        threading.Thread(target=worker, daemon=True, name="slide").start()

    def _prepare(self, now: float) -> dict[str, Any] | None:
        self._scan(now)
        if not self._files:
            return None
        for _ in range(min(len(self._files), 8)):
            path = self._files[self._index % len(self._files)]
            self._index = (self._index + 1) % len(self._files)
            data = self._encode(
                path, self.section.width, self.section.height, self.section.quality
            )
            if data:
                return {"src": data, "caption": path.stem}
            if not self._no_pillow:
                self._no_pillow = True
                self.log(
                    f"could not prepare {path.name} - pillow not installed? "
                    "(pip install -r requirements-windows.txt)"
                )
        return None
