"""Constructs the View HTML from configuration.

The screen is `templates/painel.html` with placeholders. Here we only interpolate
what comes from configuration: title, featured action, shortcut grid, SVG
symbols used, and which cards exist (clock, weather). The rest -- CSS, clock,
gestures, touch feedback, offline indicator -- is handled by the template.
"""

from __future__ import annotations

import html
from pathlib import Path

from .config import Action, Config
from .icons import ICONS, PLAY_STATE_ICONS, symbol_def

DEFAULT_ACCENT = "#5b4de0"

# Clock card: `view.show_clock: false` omits the card on page 1.
CLOCK_CARD = """<div class="relogio">
        <div id="hora">--:--</div>
        <div class="datas">
          <div id="dia"></div>
          <div id="data"></div>
        </div>
      </div>"""

# Weather card: included on page 1 only when weather is enabled in configuration.
WEATHER_CARD = '''<div class="card">
        <div class="topo">
          <svg id="icone" aria-hidden="true"><use href="#i-parcial"/></svg>
          <div id="temp">--</div>
        </div>
        <div id="cond">aguardando dados</div>
        <div class="meta"><span id="cidade">weather</span><span id="atual"></span></div>
      </div>'''


def _esc(value: object) -> str:
    return html.escape(str(value), quote=True)


def _tint(color: str, alpha: str = "1f") -> str:
    """`#rrggbb` + alpha for icon square background."""
    return f"{color}{alpha}"


def _icon_attr(action: Action) -> tuple[str, str]:
    """Returns (symbol name, optional inline svg)."""
    if action.icon_svg:
        return f"custom_{action.id}", action.icon_svg
    return action.icon if action.icon in ICONS else "dot", ""


def button_html(action: Action) -> str:
    """A grid button (page 2)."""
    classes = ["btn", "btn-clicavel"]
    # in the page 2 grid the featured action also gets highlight styling
    if action.accent or action.featured:
        classes.append("destaque")
    symbol, svg_body = _icon_attr(action)
    if svg_body:
        # custom standalone icon: render direct <svg> without symbol
        icon = f'<svg viewBox="0 0 24 24">{svg_body}</svg>'
    else:
        state_class = "ic-pp" if action.icon in PLAY_STATE_ICONS else ""
        icon = f'<svg class="{state_class}"><use href="#i-{_esc(symbol)}"/></svg>'

    style = ""
    if action.color:
        style = f' style="color:{action.color};background:{_tint(action.color)}"'
    return (
        f'<button class="{" ".join(classes)}" data-acao="{_esc(action.id)}"'
        f' data-rotulo="{_esc(action.label)}">\n'
        f'          <span class="ico"{style}>{icon}</span>\n'
        f'          <span class="lbl">{_esc(action.label)}</span>\n'
        f"        </button>"
    )


def featured_html(action: Action | None) -> str:
    """The large button on page 1. Shows cover art and track title for media actions."""
    if action is None:
        return ""
    symbol, svg_body = _icon_attr(action)
    if svg_body:
        icon = f'<svg viewBox="0 0 24 24">{svg_body}</svg>'
    else:
        icon = f'<svg class="ic-pp"><use href="#i-{_esc(symbol)}"/></svg>'

    if action.type == "media":
        # media banner: cover, track title, and state (populated by update_view)
        return (
            f'<button class="btn-midia-banner btn-clicavel featured" '
            f'data-acao="{_esc(action.id)}" data-rotulo="{_esc(action.label)}">\n'
            '        <div id="banner-capa" class="capa-banner"></div>\n'
            '        <span class="ico-midia">' + icon + "</span>\n"
            '        <span class="txt-midia">\n'
            '          <span id="faixa">Mídia no computador</span>\n'
            '          <span id="sub">aguardando o player</span>\n'
            "        </span>\n"
            "      </button>"
        )
    # any other featured action: large button without cover
    return (
        f'<button class="btn destaque featured btn-clicavel" '
        f'data-acao="{_esc(action.id)}" data-rotulo="{_esc(action.label)}">\n'
        f'        <span class="ico">{icon}</span>\n'
        f'        <span class="lbl">{_esc(action.label)}</span>\n'
        "      </button>"
    )


def _switch_row(
    name: str,
    turn_on: Action,
    turn_off: Action,
    title: str,
    off_text: str,
    on_text: str,
) -> str:
    """A large switch toggle. Touch invokes the action for the NEW state."""
    return (
        f'<button id="chave-{name}" class="config-item btn-clicavel" '
        f'data-acao="{_esc(turn_on.id)}" data-acao-ligar="{_esc(turn_on.id)}" '
        f'data-acao-desligar="{_esc(turn_off.id)}" '
        f'data-texto-desligado="{_esc(off_text)}" '
        f'data-texto-ligado="{_esc(on_text)}" '
        f'data-rotulo="{_esc(title)}">\n'
        f'          <span class="switch" id="switch-{name}"><i></i></span>\n'
        '          <span class="cfg-txt">\n'
        f"            <b>{_esc(title)}</b>\n"
        f'            <small id="cfg-{name}">{_esc(off_text)}</small>\n'
        "          </span>\n"
        "        </button>"
    )


def _pair(actions: list[Action], action_type: str) -> tuple[Action, Action] | None:
    matching = [a for a in actions if a.type == action_type]
    on_action = next((a for a in matching if a.params.get("state") is True), None)
    off_action = next((a for a in matching if a.params.get("state") is False), None)
    return (on_action, off_action) if on_action and off_action else None


_par = _pair


def settings_html(config: Config) -> str:
    """Page 3 (settings): one switch toggle per action pair (`rest`, `overlay`).

    The true state lives on the PC -- the screen reflects what `update_view` sends.
    """
    lines: list[str] = []

    rest_pair = _pair(config.page3, "rest")
    if rest_pair:
        lines.append(_switch_row(
            "descanso",
            rest_pair[0],
            rest_pair[1],
            "Modo descanso",
            "Desligado — a tela fica sempre acesa",
            f"Ligado — a tela escurece depois de {config.rest.idle_s} s sem toque",
        ))

    overlay_pair = _pair(config.page3, "overlay")
    if overlay_pair:
        lines.append(_switch_row(
            "relogio",
            overlay_pair[0],
            overlay_pair[1],
            "Relógio sobre as fotos",
            "Desligado — a foto fica limpa, sem nada por cima",
            "Ligado — hora grande e translúcida sobre a imagem",
        ))

    if lines:
        return "\n        ".join(lines)
    # configuration without full pairs: display whatever exists
    return "\n        ".join(button_html(a) for a in config.page3)


def symbols_html(config: Config) -> str:
    """Only symbols referenced by configured actions (weather symbols are in template)."""
    parts: list[str] = []
    used: set[str] = set()
    for action in config.actions:
        if action.icon_svg:
            continue
        symbol, _ = _icon_attr(action)
        if symbol not in used:
            used.add(symbol)
            parts.append(symbol_def(symbol))
        # media button toggles play/pause icon dynamically
        if action.icon in PLAY_STATE_ICONS:
            for extra in ("play", "pause"):
                if extra not in used:
                    used.add(extra)
                    parts.append(symbol_def(extra))
    return "\n  ".join(parts)


SHORTCUTS_PAGE = """<section class="pagina" id="p2">
      <div id="topo-p2">
        <div id="titulo">Atalhos</div>
      </div>
      <div id="grid-atalhos">
        {botoes}
      </div>
    </section>"""
ATALHOS_PAGE = SHORTCUTS_PAGE

SETTINGS_PAGE = """<section class="pagina" id="p3">
      <div id="topo-p3">
        <div id="titulo3">Ajustes</div>
      </div>
      <div id="config-lista">
        {acoes}
      </div>
    </section>"""
AJUSTES_PAGE = SETTINGS_PAGE

# Slideshow is an EMPTY page: the photo is a fixed background behind everything,
# filling full screen (no card border), and footer floats on top.
SLIDES_PAGE = """<section class="pagina" id="p0"></section>

<div class="slide-fundo" id="slide-fundo">
  <div class="slide-camada" id="slide-a"></div>
  <div class="slide-camada" id="slide-b"></div>
  <div id="relogio-slide" aria-hidden="true">
    <div id="hora-slide">--:--</div>
    <div id="data-slide"></div>
  </div>
  <div id="slide-progresso" aria-hidden="true">
    <svg viewBox="0 0 36 36">
      <circle class="anel-trilha" cx="18" cy="18" r="15"></circle>
      <circle class="anel-arco" id="anel-arco" cx="18" cy="18" r="15"></circle>
    </svg>
  </div>
  {legenda}
</div>"""


def slides_page_html(
    config: Config,
    has_images: bool | None = None,
    tem_imagens: bool | None = None,
) -> str:
    """Slideshow page exists only when there are images to show."""
    if not config.slides.enabled:
        return ""
    flag = has_images if has_images is not None else tem_imagens
    if flag is False:
        return ""
    caption = '<div id="slide-legenda"></div>' if config.slides.caption else ""
    return SLIDES_PAGE.format(legenda=caption)


def slides_background_html(
    config: Config,
    has_images: bool | None = None,
    tem_imagens: bool | None = None,
) -> str:
    """Fixed background: sibling of #app, behind everything, in fullscreen."""
    page = slides_page_html(config, has_images=has_images, tem_imagens=tem_imagens)
    if not page:
        return ""
    start = page.find('<div class="slide-fundo"')
    return page[start:] if start >= 0 else ""


def render(
    config: Config,
    template: Path | None = None,
    has_slides: bool | None = None,
    tem_slides: bool | None = None,
) -> str:
    """Reads the template and returns the final HTML for `show_view`."""
    path = template or config.view.template
    try:
        html_template = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        raise FileNotFoundError(f"View template not found: {path} ({exc})") from None

    slides_flag = has_slides if has_slides is not None else tem_slides

    p1: list[str] = []
    if config.view.show_clock:
        p1.append("com-relogio")
    if config.view.show_weather and config.weather.enabled:
        p1.append("com-clima")
    featured = config.featured
    if featured is not None:
        p1.append("com-destaque")

    weather = WEATHER_CARD if (config.view.show_weather and config.weather.enabled) else ""
    clock = CLOCK_CARD if config.view.show_clock else ""

    # Each track page exists only when it has content: an empty section would
    # create a blank snap stop during swipe gestures.
    slides_page = slides_page_html(config, has_images=slides_flag)
    slides_bg = slides_background_html(config, has_images=slides_flag)
    shortcuts_page = (
        SHORTCUTS_PAGE.format(
            botoes="\n        ".join(button_html(a) for a in config.page2)
        )
        if config.page2 else ""
    )
    settings_page = (
        SETTINGS_PAGE.format(acoes=settings_html(config)) if config.page3 else ""
    )

    if slides_page:
        slides_page = '<section class="pagina" id="p0"></section>'

    order = ["relogio"]
    if slides_page:
        order.insert(0, "apresentacao")
    if shortcuts_page:
        order.append("atalhos")
    if settings_page:
        order.append("ajustes")
    dots = "".join(
        f'<button class="ponto{" on" if i == 0 else ""}" data-p="{i}" '
        f'aria-label="Page {i + 1}"><i></i></button>'
        for i in range(len(order))
    )

    replacements = {
        "{{TITLE}}": _esc(config.view.title),
        "{{SYMBOLS}}": symbols_html(config),
        "{{P1_CLASSES}}": " ".join(p1),
        "{{CLOCK_CARD}}": clock,
        "{{WEATHER_CARD}}": weather,
        "{{FEATURED}}": featured_html(featured),
        "{{PAGINA_SLIDES}}": slides_page,
        "{{PAGINA_SLIDES_FUNDO}}": slides_bg,
        "{{PAGINA_ATALHOS}}": shortcuts_page,
        "{{PAGINA_AJUSTES}}": settings_page,
        "{{PONTOS}}": dots,
        "{{DESCANSO_ATIVO}}": "1" if config.rest.enabled else "0",
        "{{DESCANSO_SEGUNDOS}}": str(config.rest.idle_s),
        "{{ACCENT}}": (featured.color if featured and featured.color else DEFAULT_ACCENT),
    }
    out = html_template
    for marker, value in replacements.items():
        out = out.replace(marker, value)
    return out
