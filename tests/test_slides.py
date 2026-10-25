"""Slideshow page: looping image folder."""

import pytest

from mcp_display_companion import view
from mcp_display_companion.config import (
    Action,
    ConfigError,
    PanelSection,
    RestSection,
    SlidesSection,
)
from mcp_display_companion.slideshow import SlideDeck


# --------------------------------------------------------------------------- #
# configuration
# --------------------------------------------------------------------------- #
def test_slides_are_off_by_default(load_config, raw_config):
    cfg = load_config(raw_config)
    assert cfg.slides.enabled is False
    assert cfg.slides.interval_s == 20


def test_folder_turns_the_page_on(load_config, raw_config, tmp_path):
    raw_config["slides"] = {"folder": str(tmp_path)}
    cfg = load_config(raw_config)
    assert cfg.slides.enabled is True
    assert cfg.slides.folder == tmp_path


def test_explicit_false_keeps_it_off(load_config, raw_config, tmp_path):
    raw_config["slides"] = {"folder": str(tmp_path), "enabled": False}
    assert load_config(raw_config).slides.enabled is False


def test_slides_section_values(load_config, raw_config, tmp_path):
    raw_config["slides"] = {
        "folder": str(tmp_path), "interval_s": 45, "shuffle": True,
        "width": 800, "height": 400, "quality": 60, "rescan_s": 60,
    }
    cfg = load_config(raw_config).slides
    assert (cfg.interval_s, cfg.shuffle, cfg.width, cfg.height, cfg.quality, cfg.rescan_s) == (
        45, True, 800, 400, 60, 60)


@pytest.mark.parametrize("field,value", [
    ("interval_s", 1), ("width", 100), ("height", 100), ("quality", 20),
    ("quality", 96), ("rescan_s", 1),
])
def test_invalid_slides_values(load_config, raw_config, tmp_path, field, value):
    raw_config["slides"] = {"folder": str(tmp_path), field: value}
    with pytest.raises(ConfigError, match="slides"):
        load_config(raw_config)


# --------------------------------------------------------------------------- #
# the folder
# --------------------------------------------------------------------------- #
def fake_encoder(marker="ok"):
    def encode(path, width, height, quality):
        return f"data:image/jpeg;base64,{marker}:{path.name}:{width}x{height}"

    return encode


def deck_with_files(tmp_path, names, **kwargs):
    for name in names:
        (tmp_path / name).write_bytes(b"placeholder")
    encoders = kwargs.pop("encode", fake_encoder())
    shuffle_fn = kwargs.pop("shuffle_fn", kwargs.pop("sortear", None))
    async_mode = kwargs.pop("async_mode", kwargs.pop("assincrono", False))
    kwargs.setdefault("interval_s", 10)
    section = SlidesSection(enabled=True, folder=tmp_path, **kwargs)
    # synchronous by default: deterministic order in tests
    return SlideDeck(section, encode=encoders, shuffle=shuffle_fn, async_mode=async_mode)


deck_com_arquivos = deck_with_files


def test_only_images_are_listed(tmp_path):
    deck = deck_with_files(tmp_path, ["a.jpg", "b.PNG", "c.webp", "nota.txt", "video.mp4"])
    assert [p.name for p in deck._files] == []
    assert deck.has_images() is True
    assert sorted(p.name for p in deck._files) == ["a.jpg", "b.PNG", "c.webp"]


def test_empty_folder_has_no_images(tmp_path):
    deck = deck_with_files(tmp_path, [])
    assert deck.has_images() is False
    assert deck.due(1000) is None


def test_missing_folder_does_not_break(tmp_path):
    section = SlidesSection(enabled=True, folder=tmp_path / "absent")
    deck = SlideDeck(section, encode=fake_encoder(), async_mode=False)
    assert deck.has_images() is False
    assert deck.due(1000) is None


def test_images_loop_in_order(tmp_path):
    deck = deck_with_files(tmp_path, ["a.jpg", "b.jpg", "c.jpg"])
    deck.prepare_now()
    captions = []
    now = 1000
    for _ in range(4):
        captions.append(deck.due(now)["caption"])
        now += 10
    assert captions == ["a", "b", "c", "a"]


def test_interval_is_respected(tmp_path):
    deck = deck_with_files(tmp_path, ["a.jpg", "b.jpg"], interval_s=30)
    deck.prepare_now()
    assert deck.due(1000) is not None
    assert deck.due(1010) is None          # before interval
    assert deck.due(1000 + 31) is not None


def test_broken_file_is_skipped(tmp_path):
    deck = deck_with_files(tmp_path, ["broken.jpg", "good.jpg"],
                           encode=lambda path, *a: None if path.name.startswith("broken") else "data:x")
    slide = deck.prepare_now()
    assert slide == {"src": "data:x", "caption": "good"}


def test_preparation_happens_off_the_caller(tmp_path):
    """Compression must not block loop: first call only requests preparation."""
    import time

    def slow_encode(path, *a):
        time.sleep(0.4)
        return "data:x"

    deck = deck_with_files(tmp_path, ["a.jpg"], encode=slow_encode, async_mode=True)
    started = time.time()
    assert deck.due(time.time()) is None       # requested, did not wait
    assert time.time() - started < 0.2
    for _ in range(40):
        time.sleep(0.05)
        slide = deck.due(time.time())
        if slide:
            break
    assert slide == {"src": "data:x", "caption": "a"}


def test_new_file_is_picked_up_without_restart(tmp_path):
    deck = deck_with_files(tmp_path, ["a.jpg"], rescan_s=300)
    deck.prepare_now(1000)
    (tmp_path / "b.jpg").write_bytes(b"x")
    deck.prepare_now(1030)      # inside rescan window: does not rescan
    assert len(deck._files) == 1
    deck.prepare_now(2000)      # after rescan: picks up new photo
    assert len(deck._files) == 2


def test_shuffle_uses_the_sorter(tmp_path):
    marked = {}

    def sorter(items):
        marked["called"] = True
        items.reverse()

    deck = deck_with_files(tmp_path, ["a.jpg", "b.jpg", "c.jpg"], shuffle=True,
                           shuffle_fn=sorter)
    deck.has_images()
    assert marked.get("called") is True
    assert [p.name for p in deck._files] == ["c.jpg", "b.jpg", "a.jpg"]


# --------------------------------------------------------------------------- #
# the view page
# --------------------------------------------------------------------------- #
def page_config(make_config, **kwargs):
    """Single action on page 1: no shortcuts, so extra pages are represented by dots."""
    return make_config(
        [Action("pc_key", "Key", "key", params={"vk": 175}, pages=(1,), featured=True)],
        **kwargs,
    )


pagina_config = page_config


def test_page_is_rendered_before_the_clock(make_config, tmp_path):
    html = view.render(page_config(make_config, slides=SlidesSection(enabled=True, folder=tmp_path)))
    assert 'id="p0"' in html
    assert html.index('id="p0"') < html.index('id="p1"')
    assert "slide-camada" in html
    assert html.count('data-p="') == 2   # slideshow + clock


def test_page_disappears_without_images(make_config, tmp_path):
    html = view.render(
        page_config(make_config, slides=SlidesSection(enabled=True, folder=tmp_path)),
        has_slides=False,
    )
    assert 'id="p0"' not in html
    assert html.count('data-p="') == 1


def test_the_photo_is_a_fullscreen_background(make_config, tmp_path):
    """Photo is fixed background behind everything: page 0 is empty."""
    html = view.render(page_config(make_config, slides=SlidesSection(enabled=True, folder=tmp_path)))
    assert html.index('id="slide-fundo"') < html.index('id="app"')
    start = html.index('<section class="pagina" id="p0">')
    end = html.index("</section>", start)
    assert "slide-camada" not in html[start:end]


def test_caption_is_off_by_default(make_config, tmp_path):
    without = view.render(page_config(make_config, slides=SlidesSection(enabled=True, folder=tmp_path)))
    assert 'id="slide-legenda"' not in without

    with_caption = view.render(page_config(
        make_config, slides=SlidesSection(enabled=True, folder=tmp_path, caption=True)))
    assert 'id="slide-legenda"' in with_caption


def test_page_counts_with_all_pages(make_config, tmp_path):
    cfg = make_config(
        [Action("pc_key", "Key", "key", params={"vk": 175}, pages=(2,)),
         Action("pc_descanso_ativar", "Ativar", "rest", pages=(3,), params={"state": True}),
         Action("pc_descanso_desativar", "Desativar", "rest", pages=(3,), params={"state": False})],
        slides=SlidesSection(enabled=True, folder=tmp_path),
        rest=RestSection(enabled=False, idle_s=60),
    )
    html = view.render(cfg)
    assert html.count('data-p="') == 4
    # track order: slideshow, clock, shortcuts, settings
    order = [html.index(f'id="p{i}"') for i in range(4)]
    assert order == sorted(order)


# --------------------------------------------------------------------------- #
# sending by service
# --------------------------------------------------------------------------- #
def test_dark_screen_stops_sending_slides(tmp_path, monkeypatch):
    """In rest mode screen is dimmed: skip sending photos."""
    from mcp_display_companion.config import Action, Config, DisplaySection, PanelSection

    class UnusedLoop:
        def run_until_complete(self, coro):
            raise AssertionError("unexpected async call")

        def close(self):
            pass

    monkeypatch.setattr("mcp_display_companion.runtime.asyncio.new_event_loop", UnusedLoop)

    class FakeClient:
        def __init__(self):
            self.updates = []

        def update_view(self, data, presentation=None):
            self.updates.append(data)

        def read_events(self, since):
            return {'cursor': since if since is not None else 0, 'eventos': []}

        def set_display(self, brightness):
            pass

    class EmptyHost:
        pass

    class EmptyLive:
        def due(self, now, force=False):
            return {}

        def ack(self, data):
            pass

        def snapshot(self):
            return {}

    class FakeDeck:
        def __init__(self):
            self.sent = 0
            self.changed = False

        def check_changed(self, now):
            return self.changed

        def verificar(self, now):
            return self.changed

        def due(self, now, force=False):
            self.sent += 1
            return {"src": "data:x", "caption": "photo"}

    from mcp_display_companion.runtime import Runtime

    cfg = Config(
        panel=PanelSection("http://127.0.0.1/mcp", "t", cursor_file=tmp_path / "c.json"),
        actions=[Action("pc_key", "Key", "key", params={"vk": 175})],
        display=DisplaySection(enabled=False),
        state_file=tmp_path / "state.json",
    )
    client = FakeClient()
    rt = Runtime(cfg, client=client, host=EmptyHost(), live=EmptyLive(), clock=lambda: 1000.0)
    rt.slides = FakeDeck()
    rt.screen_dark = True
    rt.tick()
    assert not any("slide" in data for data in client.updates)
    rt.screen_dark = False
    rt.tick()
    sent_slides = [data["slide"] for data in client.updates if "slide" in data]
    assert sent_slides, "photo should have been sent"
    assert sent_slides[0]["interval"] == cfg.slides.interval_s


def test_verificar_detects_new_images(tmp_path):
    """Page appears when first image is dropped in folder."""
    deck = deck_with_files(tmp_path, [], rescan_s=10)
    assert deck.check_changed(1000) is False
    assert deck.has_images() is False

    (tmp_path / "first.jpg").write_bytes(b"x")
    assert deck.check_changed(1100) is True

    (tmp_path / "second.jpg").write_bytes(b"x")
    assert deck.check_changed(1200) is False

    for file in tmp_path.iterdir():
        file.unlink()
    assert deck.check_changed(1300) is True
