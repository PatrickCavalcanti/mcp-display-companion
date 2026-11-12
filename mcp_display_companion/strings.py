"""Panel texts, one dictionary per language.

Every visible string of the screen lives here; the template and `view.py`
reference them by key. Keys are English snake_case, grouped by prefix
(`status_`, `clock_`, `weather_`, `media_`, `shortcuts_`, `settings_`,
`home_`, `alert_`, `nav_`). Both languages must define exactly the same keys
(`tests/test_strings.py` enforces it).

Values are `str.format` templates. Action labels and `view.title` come from the
user's configuration and are never translated.
"""

from __future__ import annotations

LANGUAGES = ("en", "pt-BR")
DEFAULT_LANGUAGE = "en"

_EN = {
    # footer / connection state
    "status_connecting": "connecting",
    "status_ready": "ready",
    "status_handshake_failed": "handshake failed",
    "status_pc": "PC",
    "status_pc_offline": "PC offline",
    "status_too_many_taps": "too many taps, wait",
    "status_sending": "sending: {label}",
    "status_no_reply": "no reply from PC",
    "status_refused": "refused: {label}",
    "status_done": "done: {label}",
    "status_failed": "failed: {label}",
    # clock page
    "clock_day_0": "Sunday",
    "clock_day_1": "Monday",
    "clock_day_2": "Tuesday",
    "clock_day_3": "Wednesday",
    "clock_day_4": "Thursday",
    "clock_day_5": "Friday",
    "clock_day_6": "Saturday",
    "clock_month_0": "January",
    "clock_month_1": "February",
    "clock_month_2": "March",
    "clock_month_3": "April",
    "clock_month_4": "May",
    "clock_month_5": "June",
    "clock_month_6": "July",
    "clock_month_7": "August",
    "clock_month_8": "September",
    "clock_month_9": "October",
    "clock_month_10": "November",
    "clock_month_11": "December",
    "clock_date": "{month} {day}, {year}",
    # weather card
    "weather_waiting": "waiting for data",
    "weather_city_placeholder": "weather",
    "weather_updated": "updated {time}",
    # media banner
    "media_default_title": "PC media",
    "media_waiting_player": "waiting for the player",
    "media_playing": "Playing",
    "media_paused": "Paused",
    "media_action_pause": "Pause",
    "media_action_play": "Play",
    "media_state_playing": "playing",
    "media_state_paused": "paused",
    # shortcuts page
    "shortcuts_title": "Shortcuts",
    # settings page
    "settings_title": "Settings",
    "settings_rest_title": "Rest mode",
    "settings_rest_off": "Off — the screen stays on",
    "settings_rest_on": "On — the screen dims after {seconds} s without touch",
    "settings_clock_title": "Clock over the photos",
    "settings_clock_off": "Off — the photo stays clean, nothing on top",
    "settings_clock_on": "On — large translucent clock over the image",
    # home control page
    "home_air": "Air",
    "home_light": "Light",
    "home_temp_down": "Lower temperature",
    "home_temp_up": "Raise temperature",
    "home_waiting": "waiting for Home Assistant",
    "home_on": "On",
    "home_off": "Off",
    "home_lamp_on": "On",
    "home_lamp_off": "Off",
    "home_room": "Room {temp}",
    "home_decimal_separator": ".",
    "home_updated": "updated {time}",
    "home_unavailable": "Home Assistant unavailable",
    "home_not_found": 'could not find "{climate}" or "{light}" (expose them to Assist)',
    "home_sending": "sending…",
    "home_refused": "failed: Home Assistant refused",
    "home_connecting": "connecting…",
    "home_loading": "loading…",
    "home_no_target": "no target temperature in this mode",
    # Claude Code permission banner
    "alert_asks_permission": "{project} asks for permission",
    "alert_wants_tool": "Claude wants to use {tool}",
    "alert_dismiss": "dismiss",
    "alert_deny": "Deny",
    "alert_approve_hold": "Hold to approve",
    "alert_sent": "sent…",
    "alert_scroll_to_approve": "Scroll to the end of the text to enable Approve",
    "alert_long_text": "Long or hidden text — approve on the PC",
    "alert_no_key": "No reply key — answer on the PC",
    "alert_no_connection": "No connection to the PC — nothing was sent",
    "alert_too_many_taps": "Too many taps, wait",
    # page dots
    "nav_page": "Page {n}",
}

_PT_BR = {
    "status_connecting": "conectando",
    "status_ready": "pronto",
    "status_handshake_failed": "falha no handshake",
    "status_pc": "PC",
    "status_pc_offline": "PC offline",
    "status_too_many_taps": "muitos toques, aguarde",
    "status_sending": "enviando: {label}",
    "status_no_reply": "sem resposta do PC",
    "status_refused": "recusado: {label}",
    "status_done": "feito: {label}",
    "status_failed": "falhou: {label}",
    "clock_day_0": "Domingo",
    "clock_day_1": "Segunda-feira",
    "clock_day_2": "Terça-feira",
    "clock_day_3": "Quarta-feira",
    "clock_day_4": "Quinta-feira",
    "clock_day_5": "Sexta-feira",
    "clock_day_6": "Sábado",
    "clock_month_0": "janeiro",
    "clock_month_1": "fevereiro",
    "clock_month_2": "março",
    "clock_month_3": "abril",
    "clock_month_4": "maio",
    "clock_month_5": "junho",
    "clock_month_6": "julho",
    "clock_month_7": "agosto",
    "clock_month_8": "setembro",
    "clock_month_9": "outubro",
    "clock_month_10": "novembro",
    "clock_month_11": "dezembro",
    "clock_date": "{day} de {month} de {year}",
    "weather_waiting": "aguardando dados",
    "weather_city_placeholder": "weather",
    "weather_updated": "atualizado {time}",
    "media_default_title": "Mídia no computador",
    "media_waiting_player": "aguardando o player",
    "media_playing": "Tocando",
    "media_paused": "Pausado",
    "media_action_pause": "Pausar",
    "media_action_play": "Tocar",
    "media_state_playing": "tocando",
    "media_state_paused": "pausado",
    "shortcuts_title": "Atalhos",
    "settings_title": "Ajustes",
    "settings_rest_title": "Modo descanso",
    "settings_rest_off": "Desligado — a tela fica sempre acesa",
    "settings_rest_on": "Ligado — a tela escurece depois de {seconds} s sem toque",
    "settings_clock_title": "Relógio sobre as fotos",
    "settings_clock_off": "Desligado — a foto fica limpa, sem nada por cima",
    "settings_clock_on": "Ligado — hora grande e translúcida sobre a imagem",
    "home_air": "Ar",
    "home_light": "Luz",
    "home_temp_down": "Diminuir temperatura",
    "home_temp_up": "Aumentar temperatura",
    "home_waiting": "aguardando o Home Assistant",
    "home_on": "Ligado",
    "home_off": "Desligado",
    "home_lamp_on": "Acesa",
    "home_lamp_off": "Apagada",
    "home_room": "Ambiente {temp}",
    "home_decimal_separator": ",",
    "home_updated": "atualizado {time}",
    "home_unavailable": "Home Assistant indisponível",
    "home_not_found": 'não achei "{climate}" nem "{light}" (exponha ao Assist)',
    "home_sending": "enviando…",
    "home_refused": "falhou: o Home Assistant recusou",
    "home_connecting": "conectando…",
    "home_loading": "carregando…",
    "home_no_target": "sem temperatura alvo neste modo",
    "alert_asks_permission": "{project} pede permissão",
    "alert_wants_tool": "Claude quer usar {tool}",
    "alert_dismiss": "dispensar",
    "alert_deny": "Negar",
    "alert_approve_hold": "Segure p/ aprovar",
    "alert_sent": "enviado…",
    "alert_scroll_to_approve": "Role o texto até o fim para liberar Aprovar",
    "alert_long_text": "Texto longo/oculto — aprove no PC",
    "alert_no_key": "Sem chave de resposta — responda no PC",
    "alert_no_connection": "Sem conexão com o PC — nada foi enviado",
    "alert_too_many_taps": "Muitos toques, aguarde",
    "nav_page": "Page {n}",
}

STRINGS: dict[str, dict[str, str]] = {"en": _EN, "pt-BR": _PT_BR}


# WMO weather codes (open-meteo) -> condition text. Read only by Python (`Weather`),
# so it stays out of the dictionaries that reach the screen script as `T`. Both
# languages must cover the same codes; `iconeClima` in the template picks the icon
# from keywords of the text, and `tests/test_strings.py` checks both texts of a code
# give the same icon.
WEATHER_CONDITIONS: dict[str, dict[int, str]] = {
    "en": {
        0: "Clear sky",
        1: "Mostly clear",
        2: "Partly cloudy",
        3: "Overcast",
        45: "Fog",
        48: "Fog",
        51: "Light rain",
        53: "Rain",
        55: "Rain",
        61: "Rain",
        63: "Rain",
        65: "Heavy rain",
        80: "Showers",
        81: "Showers",
        82: "Heavy showers",
        95: "Thunderstorm",
        96: "Thunderstorm",
        99: "Thunderstorm",
    },
    "pt-BR": {
        0: "Céu limpo",
        1: "Parcialmente nublado",
        2: "Parcialmente nublado",
        3: "Nublado",
        45: "Nevoeiro",
        48: "Nevoeiro",
        51: "Chuva fraca",
        53: "Chuva",
        55: "Chuva",
        61: "Chuva",
        63: "Chuva",
        65: "Chuva forte",
        80: "Pancadas",
        81: "Pancadas",
        82: "Pancadas fortes",
        95: "Tempestade",
        96: "Tempestade",
        99: "Tempestade",
    },
}


def weather_condition(language: str, code: object) -> str:
    """Condition text of a WMO `code` in `language`; "-" for an unknown code."""
    return WEATHER_CONDITIONS[language].get(code, "-")  # type: ignore[arg-type]


def t(language: str, key: str, **kwargs: object) -> str:
    """Text for `key` in `language`, formatted with `kwargs`.

    An unknown language or key raises `KeyError`: a typo must fail in a test,
    not show up on the screen.
    """
    return STRINGS[language][key].format(**kwargs)


def all_texts(language: str) -> dict[str, str]:
    """Every raw (unformatted) text of a language; the screen script reads it as `T`."""
    return dict(STRINGS[language])
