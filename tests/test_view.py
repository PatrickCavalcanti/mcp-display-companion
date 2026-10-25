from html.parser import HTMLParser
import pytest
from mcp_display_companion.config import Action, ViewSection, WeatherSection
from mcp_display_companion.view import render


class Elements(HTMLParser):
    def __init__(self, text):
        super().__init__()
        self.tags = []
        self.feed(text)

    def handle_starttag(self, tag, attrs):
        self.tags.append((tag, dict(attrs)))


def test_button_escaping(make_config):
    action = Action('pc_key', 'A & "B"', 'key', pages=(2,))
    text = render(make_config([action]))
    buttons = [attrs for tag, attrs in Elements(text).tags if tag == 'button']
    assert any(b.get('data-acao') == action.id and b.get('data-rotulo') == action.label for b in buttons)
    assert 'data-rotulo="A &amp; &quot;B&quot;"' in text


@pytest.mark.parametrize('kind,featured,banner', [('media', True, True), ('key', True, False), ('media', False, False)])
def test_featured(make_config, kind, featured, banner):
    text = render(make_config([Action('pc_action', 'Action', kind, featured=featured)]))
    buttons = [a for t, a in Elements(text).tags if t == 'button']
    assert any('btn-midia-banner' in a.get('class', '') for a in buttons) == banner
    assert ('id="banner-capa"' in text) == banner
    if featured and kind != 'media':
        assert any('btn destaque featured' in a.get('class', '') for a in buttons)


@pytest.mark.parametrize('enabled,show,clock,featured', [\
    (True, True, True, True), (False, True, False, False),
    (True, False, True, False), (False, False, False, True),
])
def test_cards_and_page_classes(make_config, enabled, show, clock, featured):
    text = render(make_config([Action('pc_key', 'Key', 'key', featured=featured)],
                  weather=WeatherSection(enabled=enabled), view=ViewSection(show_weather=show, show_clock=clock)))
    attrs = [a for t, a in Elements(text).tags if a.get('id') == 'p1'][0]
    classes = attrs['class'].split()
    assert ('com-relogio' in classes) == clock
    assert ('com-clima' in classes) == (enabled and show)
    assert ('com-destaque' in classes) == featured
    assert ('id="temp"' in text) == (enabled and show)


@pytest.mark.parametrize('pages,dots', [((1,), 1), ((2,), 2)])
def test_complete_template_and_page_count(make_config, pages, dots):
    action = Action('pc_key', 'Key', 'key', pages=pages, featured=(pages == (1,)))
    text = render(make_config([action], view=ViewSection(title='<Title>')))
    assert '{{' not in text
    # number of pages comes from actual count of sections in HTML
    assert "var PAGINAS = document.querySelectorAll('.pagina').length;" in text
    assert text.count('data-p="') == dots
    assert '<title>&lt;Title&gt;</title>' in text


def test_custom_icon_inline(make_config):
    svg = '<path id="custom-shape" d="M0 0h10v10z"/>'
    text = render(make_config([Action('pc_key', 'Key', 'key', icon_svg=svg)]))
    assert f'<svg viewBox="0 0 24 24">{svg}</svg>' in text


@pytest.mark.parametrize('featured', [False, True])
def test_unknown_icon_falls_back(make_config, featured):
    text = render(make_config([Action('pc_key', 'Key', 'key', icon='unknown', featured=featured)]))
    assert '<use href="#i-dot"/>' in text
    assert 'id="i-dot"' in text
    assert '#i-unknown' not in text


def test_missing_template(make_config, tmp_path):
    with pytest.raises(FileNotFoundError):
        render(make_config(), tmp_path / 'absent.html')
