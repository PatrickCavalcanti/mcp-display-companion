import pytest
from mcp_display_companion import events


def classify(event, now=1000, limit=60):
    return events.classify(event, lambda tool: tool == 'pc_key', now, limit)


def test_known_touch(touch):
    result = classify(touch())
    assert result.kind == events.RUN and result.should_run
    assert (result.presentation, result.event_id, result.tool) == ('event-screen', 42, 'pc_key')


@pytest.mark.parametrize('data', [{'tool': 'other'}, {'tool': 'pc_key', 'destino_indisponivel': True}])
def test_foreign(touch, data):
    assert classify(touch(dados=data)).kind == events.FOREIGN


@pytest.mark.parametrize('age,limit,kind', [(61, 60, events.OLD), (60, 60, events.RUN), (9999, 0, events.RUN)])
def test_age_boundary(touch, age, limit, kind):
    assert classify(touch(em=(1000-age)*1000), limit=limit).kind == kind


@pytest.mark.parametrize('kind', ['acordou', 'mensagem'])
def test_other(touch, kind):
    assert classify(touch(tipo=kind)).kind == events.OTHER


@pytest.mark.parametrize('event', [None, [], 'bad', {'tipo': 'toque'}, {'tipo': 'toque', 'dados': {}}])
def test_malformed(event):
    assert classify(event).kind == events.MALFORMED
