import re
from pathlib import Path

import pytest

from mcp_display_companion import strings
from mcp_display_companion.config import default_template
from mcp_display_companion.strings import DEFAULT_LANGUAGE, LANGUAGES, STRINGS, WEATHER_CONDITIONS, t, weather_condition


def test_languages_and_default():
    assert LANGUAGES == ('en', 'pt-BR')
    assert DEFAULT_LANGUAGE == 'en'
    assert set(STRINGS) == set(LANGUAGES)


def test_same_keys_in_every_language():
    keys = {language: set(STRINGS[language]) for language in LANGUAGES}
    assert keys['en'] == keys['pt-BR']
    assert keys['en']


@pytest.mark.parametrize('language', LANGUAGES)
def test_no_empty_value(language):
    assert all(value.strip() for value in STRINGS[language].values())


@pytest.mark.parametrize('language', LANGUAGES)
def test_same_placeholders_in_every_language(language):
    slots = lambda text: sorted(re.findall(r'\{(\w+)\}', text))  # noqa: E731
    for key, value in STRINGS['en'].items():
        assert slots(STRINGS[language][key]) == slots(value), key


def test_keys_are_snake_case():
    assert all(re.fullmatch(r'[a-z0-9]+(_[a-z0-9]+)+', key) for key in STRINGS['en'])


def test_t_formats():
    assert t('en', 'status_done', label='Mute') == 'done: Mute'
    assert t('pt-BR', 'status_done', label='Mute') == 'feito: Mute'
    assert t('en', 'settings_rest_on', seconds=45) == 'On — the screen dims after 45 s without touch'
    assert t('en', 'shortcuts_title') == 'Shortcuts'


def test_t_unknown_key_or_language_fails():
    with pytest.raises(KeyError):
        t('en', 'no_such_key')
    with pytest.raises(KeyError):
        t('fr', 'shortcuts_title')


def test_t_missing_format_argument_fails():
    with pytest.raises(KeyError):
        t('en', 'status_done')


def test_all_texts_is_a_copy():
    data = strings.all_texts('en')
    data['status_ready'] = 'changed'
    assert t('en', 'status_ready') == 'ready'


def _keys_used_by_the_screen():
    """Keys referenced from the template (`{{T:key}}`, `tx('key')`) and from view.py (`t(language, "key")`)."""
    template = Path(default_template()).read_text(encoding='utf-8')
    view_py = (Path(strings.__file__).parent / 'view.py').read_text(encoding='utf-8')
    used = set(re.findall(r'\{\{T:([a-z0-9_]+)\}\}', template + view_py))
    used |= set(re.findall(r"\btx\('([a-z0-9_]*[a-z0-9])'", template))   # not the 'clock_day_' + n prefixes
    for pair in re.findall(r"\btx\(\w+ \? '([a-z0-9_]+)' : '([a-z0-9_]+)'", template):
        used |= set(pair)
    used |= set(re.findall(r't\(language, "([a-z0-9_]+)"', view_py))
    runtime_py = (Path(strings.__file__).parent / 'runtime.py').read_text(encoding='utf-8')
    used |= set(re.findall(r'(?<![\w.])t\([\w.]+, "([a-z0-9_]+)"', runtime_py))
    used |= {f'clock_day_{n}' for n in range(7)} | {f'clock_month_{n}' for n in range(12)}  # built in JS
    return used


def test_screen_only_uses_existing_keys():
    used = _keys_used_by_the_screen()
    assert used
    assert used <= set(STRINGS['en'])


def test_no_unused_key():
    """A key nobody reads is a text that was dropped from the screen: remove it."""
    assert set(STRINGS['en']) <= _keys_used_by_the_screen()


def test_weather_conditions_cover_the_same_codes_in_every_language():
    assert set(WEATHER_CONDITIONS) == set(LANGUAGES)
    assert set(WEATHER_CONDITIONS['en']) == set(WEATHER_CONDITIONS['pt-BR'])
    assert WEATHER_CONDITIONS['en']
    for language in LANGUAGES:
        assert all(text.strip() for text in WEATHER_CONDITIONS[language].values())


def test_weather_condition_unknown_code_falls_back():
    assert weather_condition('en', 3) == 'Overcast'
    assert weather_condition('pt-BR', 3) == 'Nublado'
    assert weather_condition('en', 12345) == '-'
    assert weather_condition('pt-BR', None) == '-'


def _weather_icon(text):
    """`iconeClima` of the template, run in Python: its keyword rules, in order."""
    template = Path(default_template()).read_text(encoding='utf-8')
    body = template[template.index('function iconeClima'):]
    body = body[:body.index('\n}\n')]
    rules = re.findall(r"if \(/([^/]+)/\.test\(s\)\) return '(#[\w-]+)';", body)
    fallback = re.search(r"\n  return '(#[\w-]+)';", body).group(1)
    assert len(rules) == 4
    lowered = text.lower()
    for pattern, icon in rules:
        if re.search(pattern, lowered):
            return icon
    return fallback


def test_weather_icon_is_the_same_in_every_language():
    """The icon comes from keywords of the text: both texts of a code must pick the same one."""
    for code, text in WEATHER_CONDITIONS['pt-BR'].items():
        assert _weather_icon(WEATHER_CONDITIONS['en'][code]) == _weather_icon(text), code


def test_weather_icon_table_is_pinned():
    expected = {0: '#i-sol', 1: '#i-parcial', 2: '#i-parcial', 3: '#i-nuvem', 45: '#i-nuvem', 48: '#i-nuvem',
                51: '#i-chuva', 53: '#i-chuva', 55: '#i-chuva', 61: '#i-chuva', 63: '#i-chuva', 65: '#i-chuva',
                80: '#i-chuva', 81: '#i-chuva', 82: '#i-chuva', 95: '#i-chuva', 96: '#i-chuva', 99: '#i-chuva'}
    for language in LANGUAGES:
        assert {c: _weather_icon(x) for c, x in WEATHER_CONDITIONS[language].items()} == expected, language
