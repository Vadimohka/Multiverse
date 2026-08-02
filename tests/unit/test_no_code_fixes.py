import asyncio

from app.services import source_profiler
from workflow_engine import WorkflowEngine
from workflow_engine.nodes import safe_eval
from workflow_engine.types import ExecutionContext


def test_formula_date_functions_are_safe_and_never_silent():
    yesterday = safe_eval('yesterday("Europe/Minsk")', {})
    assert len(yesterday) == 10
    assert safe_eval('format_date(yesterday("Europe/Minsk"), "YYYY-MM-DD")', {}) == yesterday
    try:
        safe_eval('unknown_function()', {})
    except ValueError as exc:
        assert 'Неизвестная функция Formula' in str(exc)
    else:
        raise AssertionError('unknown Formula function must fail visibly')


def test_source_profiler_keeps_static_result_when_rendering_is_unavailable(monkeypatch):
    class Response:
        headers = {'content-type': 'text/html'}
        status_code = 200
        content = b'<html><body>Short page</body></html>'
        text = content.decode()
        url = 'https://example.test/page'
        encoding = 'utf-8'

        def raise_for_status(self):
            pass

    class Client:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            pass

        async def get(self, *_args, **_kwargs):
            return Response()

    async def unavailable_renderer(result, _url, _timeout):
        result['rendered_text_length'] = None
        result['screenshot_available'] = False

    monkeypatch.setattr(source_profiler.httpx, 'AsyncClient', lambda **_: Client())
    monkeypatch.setattr(source_profiler, 'enrich_with_playwright', unavailable_renderer)

    result = asyncio.run(source_profiler.profile_url('https://example.test/page'))
    assert result['recommended_fetch_mode'] == 'HTTP'
    assert result['rendered_text_length'] is None


def test_set_constant_and_output_preserve_object():
    graph = {
        'nodes': [
            {'id': 'trigger', 'type': 'manual_trigger', 'config': {}},
            {'id': 'constant', 'type': 'set_constant', 'config': {'value': {'title': 'one', 'date': '2026-07-30', 'url': 'https://example.test'}}},
            {'id': 'output', 'type': 'output', 'config': {'input_path': 'records'}},
        ],
        'edges': [{'source': 'trigger', 'target': 'constant'}, {'source': 'constant', 'target': 'output'}],
    }
    result = asyncio.run(WorkflowEngine().execute(graph, ExecutionContext(run_id='1', project_id='1', workflow_version_id='1')))
    assert result['result']['records'] == [{'title': 'one', 'date': '2026-07-30', 'url': 'https://example.test'}]


def test_dataset_crud_compound_key_history_and_export(client, auth):
    project = client.get('/api/v1/projects', headers=auth).json()[0]
    created = client.post('/api/v1/datasets', headers=auth, json={
        'project_id': project['id'], 'name': 'No-code results', 'slug': 'no-code-results',
        'natural_key_fields': ['title', 'date'], 'review_policy': {'new': False, 'changed': False, 'confidence_below': 0},
    })
    assert created.status_code == 201, created.text
    dataset = created.json()
    changed = client.patch(f"/api/v1/datasets/{dataset['id']}", headers=auth, json={'natural_key_fields': ['title', 'date', 'url']})
    assert changed.status_code == 200 and changed.json()['natural_key_fields'] == ['title', 'date', 'url']
    workflow = client.post('/api/v1/workflows', headers=auth, json={
        'project_id': project['id'], 'name': 'No-code Save Dataset',
        'graph_json': {'version': 1, 'settings': {}, 'nodes': [
            {'id': 'trigger', 'type': 'manual_trigger', 'config': {}},
            {'id': 'constant', 'type': 'set_constant', 'config': {'value': {'title': 'one', 'date': '2026-07-30', 'url': 'https://example.test'}}},
            {'id': 'save', 'type': 'output', 'config': {'input_path': 'records', 'dataset_id': dataset['id']}},
        ], 'edges': [{'source': 'trigger', 'target': 'constant'}, {'source': 'constant', 'target': 'save'}]},
    })
    assert workflow.status_code == 201, workflow.text
    run = client.post(f"/api/v1/workflows/{workflow.json()['id']}/run", headers=auth, json={'synchronous': True})
    assert run.status_code == 201 and run.json()['output_json']['persistence']['created'] == 1
    records = client.get(f"/api/v1/datasets/{dataset['id']}/records", headers=auth).json()['items']
    assert records[0]['data']['title'] == 'one'
    assert client.get(f"/api/v1/records/{records[0]['id']}/history", headers=auth).json()[0]['version'] == 1
    assert client.post(f"/api/v1/exports?dataset_id={dataset['id']}&format=json", headers=auth).status_code == 200
    assert client.post(f"/api/v1/exports?dataset_id={dataset['id']}&format=xlsx", headers=auth).content[:2] == b'PK'
