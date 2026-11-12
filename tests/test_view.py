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


# --------------------------------------------------------------------------- #
# home control page
# --------------------------------------------------------------------------- #
import json
from mcp_display_companion.config import HomeSection, RestSection, SlidesSection


def _ids(text):
    return [a['id'] for t, a in Elements(text).tags if t == 'section' and a.get('id')]


def _full(make_config, tmp_path, home=True, **home_kwargs):
    actions = [Action('pc_key', 'Key', 'key', params={'vk': 175}, pages=(2,)),
               Action('pc_descanso_ativar', 'On', 'rest', pages=(3,), params={'state': True}),
               Action('pc_descanso_desativar', 'Off', 'rest', pages=(3,), params={'state': False})]
    return make_config(actions, slides=SlidesSection(enabled=True, folder=tmp_path),
                       rest=RestSection(), home=HomeSection(enabled=home, **home_kwargs))


def test_home_page_only_when_enabled(make_config, tmp_path):
    off = render(_full(make_config, tmp_path, home=False))
    assert _ids(off) == ['p0', 'p1', 'p2', 'p3']
    assert 'class="pagina pagina-ajustes" id="p3"' in off and 'id="topo-p3"' in off and 'id="titulo3"' in off
    assert 'data-casa-cfg="' not in off and '{{' not in off
    on = render(_full(make_config, tmp_path))
    assert _ids(on) == ['p0', 'p1', 'p2', 'p3', 'p4']
    assert '{{' not in on
    assert 'class="pagina pagina-casa"' in on and 'id="topo-p4"' in on and 'id="titulo4"' in on


def test_home_page_order_and_dots(make_config, tmp_path):
    text = render(_full(make_config, tmp_path))
    assert text.count('data-p="') == 5
    assert text.index('id="p2"') < text.index('id="p3"') < text.index('id="p4"')
    # symbols the page needs
    for name in ('snowflake', 'bulb', 'power', 'minus', 'plus'):
        assert f'id="i-{name}"' in text


def test_home_page_follows_shortcuts(make_config):
    cfg = make_config(home=HomeSection(enabled=True))
    assert _ids(render(cfg)) == ['p1', 'p2', 'p3']
    assert 'class="pagina pagina-casa"' in render(cfg)


def test_home_names_are_escaped(make_config, tmp_path):
    nasty = 'A"B\' <script>x</script> &'
    text = render(_full(make_config, tmp_path, title='<T&>', climate=nasty, light=nasty, area=nasty))
    assert '<script>x</script>' not in text
    assert '<T&>' not in text and '&lt;T&amp;&gt;' in text
    attrs = [a for t, a in Elements(text).tags if 'data-casa-cfg' in a][0]
    data = json.loads(attrs['data-casa-cfg'])
    assert data['climate'] == nasty and data['area'] == nasty
    assert (data['temp_min'], data['temp_max'], data['refresh_s']) == (16, 30, 30)


def test_template_without_home_marker_drops_page_and_dot(make_config, tmp_path):
    from mcp_display_companion import view
    tpl = tmp_path / 'old.html'
    tpl.write_text('<div id="trilho">{{PAGINA_ATALHOS}}{{PAGINA_AJUSTES}}</div>{{PONTOS}}', encoding='utf-8')
    cfg = _full(make_config, tmp_path)
    assert view.home_supported(cfg) is True
    assert view.home_supported(cfg, tpl) is False
    text = render(cfg, template=tpl)
    assert 'class="pagina pagina-casa"' not in text and 'data-casa-cfg' not in text
    assert 'id="p3"' in text and 'class="pagina pagina-ajustes"' in text   # settings keeps its old id
    assert text.count('data-p="') == 4  # slideshow, clock, shortcuts, settings: no dot for home
