def test_health(client):
    assert client.get('/api/v1/health').json()['status']=='ok'


def test_node_test_runs_selected_node_with_its_upstream_inputs(client, auth):
    graph = {
        'version': 1,
        'settings': {},
        'nodes': [
            {'id': 'trigger', 'type': 'manual_trigger', 'config': {}},
            {'id': 'json', 'type': 'json_path', 'config': {'input_path': 'body', 'path': '$.items[*]'}},
        ],
        'edges': [{'source': 'trigger', 'target': 'json'}],
    }
    response = client.post('/api/v1/workflows/node-test', headers=auth, json={
        'node_type': 'json_path', 'config': graph['nodes'][1]['config'],
        'graph': graph, 'target_node_id': 'json',
        'inputs': {'body': {'items': [{'title': 'one'}, {'title': 'two'}]}},
    })
    assert response.status_code == 200, response.text
    assert response.json()['result']['records'] == [{'title': 'one'}, {'title': 'two'}]


def test_manual_run_uses_saved_draft_not_an_older_published_version(client, auth):
    project = client.get('/api/v1/projects', headers=auth).json()[0]
    initial_graph = {
        'version': 1, 'settings': {},
        'nodes': [
            {'id': 'trigger', 'type': 'manual_trigger', 'config': {}},
            {'id': 'constant', 'type': 'set_constant', 'config': {'value': {'version': 'published'}}},
            {'id': 'output', 'type': 'output', 'config': {'input_path': 'records'}},
        ],
        'edges': [{'source': 'trigger', 'target': 'constant'}, {'source': 'constant', 'target': 'output'}],
    }
    workflow = client.post('/api/v1/workflows', headers=auth, json={
        'project_id': project['id'], 'name': 'Manual run draft', 'graph_json': initial_graph,
    }).json()
    assert client.post(f"/api/v1/workflows/{workflow['id']}/publish", headers=auth).status_code == 200
    draft_graph = {**initial_graph, 'nodes': [*initial_graph['nodes']]}
    draft_graph['nodes'][1] = {'id': 'constant', 'type': 'set_constant', 'config': {'value': {'version': 'draft'}}}
    updated = client.patch(f"/api/v1/workflows/{workflow['id']}", headers=auth, json={'graph_json': draft_graph})
    assert updated.status_code == 200, updated.text
    run = client.post(f"/api/v1/workflows/{workflow['id']}/run", headers=auth, json={'synchronous': True})
    assert run.status_code == 201, run.text
    assert run.json()['output_json']['result']['records'] == [{'version': 'draft'}]

def test_project_crud(client,auth):
    payload={'name':'Test Project','slug':'test-project','description':'x','template':'bank_deposits'}
    response=client.post('/api/v1/projects',json=payload,headers=auth)
    assert response.status_code==201,response.text
    items=client.get('/api/v1/projects',headers=auth).json()
    assert any(x['slug']=='test-project' for x in items)

def test_demo_workflow_run(client,auth):
    workflows=client.get('/api/v1/workflows',headers=auth).json()
    w=next(x for x in workflows if x['name']=='Нормализация депозитов')
    response=client.post(f"/api/v1/workflows/{w['id']}/run",headers=auth,json={'inputs':{'records':[{'institution_name':'Банк','product_name':'Вклад','currency':'BYN','rate':'12,5%','term':'3 месяца'}]},'synchronous':True})
    assert response.status_code==201,response.text
    data=response.json(); assert data['status']=='WAITING_FOR_REVIEW'; assert data['output_json']['result']['records'][0]['rate_value']==12.5; assert data['output_json']['persistence']['created']==1

def test_document_xlsx_parse(client,auth):
    import io

    from openpyxl import Workbook
    wb=Workbook(); ws=wb.active; ws.append(['bank','rate']); ws.append(['Demo',12.5]); buf=io.BytesIO(); wb.save(buf)
    response=client.post('/api/v1/documents/parse',headers=auth,files={'file':('rates.xlsx',buf.getvalue(),'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet')})
    assert response.status_code==200,response.text
    assert response.json()['sheets']['Sheet'][0]['bank']=='Demo'


def test_uploaded_document_can_be_run_as_a_source(client, auth):
    project = client.get('/api/v1/projects', headers=auth).json()[0]
    response = client.post(
        '/api/v1/documents/upload-source',
        headers=auth,
        data={'project_id': project['id'], 'name': 'CSV document source'},
        files={'file': ('rates.csv', 'bank,rate\nDemo,12.5\n', 'text/csv')},
    )
    assert response.status_code == 201, response.text
    source = response.json()
    assert source['fetch_mode'] == 'DOCUMENT'

    workflow = client.post('/api/v1/workflows/from-source', headers=auth, json={'source_id': source['id']}).json()
    response = client.post(f"/api/v1/workflows/{workflow['id']}/run", headers=auth, json={'synchronous': True})
    assert response.status_code == 201, response.text
    assert response.json()['status'] == 'SUCCESS'
    assert response.json()['output_json']['result']['records'] == [{'bank': 'Demo', 'rate': '12.5'}]


def test_unchanged_workflow_does_not_create_a_new_version(client, auth):
    workflow = client.get('/api/v1/workflows', headers=auth).json()[0]
    response = client.patch(
        f"/api/v1/workflows/{workflow['id']}",
        headers=auth,
        json={'graph_json': workflow['graph_json']},
    )
    assert response.status_code == 200, response.text
    assert response.json()['version'] == workflow['version']


def test_review_and_export(client,auth):
    tasks=client.get('/api/v1/review',headers=auth).json()
    if tasks:
        response=client.post(f"/api/v1/review/{tasks[0]['id']}/approve",headers=auth,json={'comment':'verified'})
        assert response.status_code==200,response.text
    datasets=client.get('/api/v1/datasets',headers=auth).json()
    dataset=next(x for x in datasets if x['slug']=='demo-deposits')
    response=client.post(f"/api/v1/exports?dataset_id={dataset['id']}&format=xlsx",headers=auth)
    assert response.status_code==200
    assert response.content[:2]==b'PK'


def test_rejected_new_record_is_not_published_or_exported(client, auth):
    workflows = client.get('/api/v1/workflows', headers=auth).json()
    workflow = next(item for item in workflows if item['name'] == 'Нормализация депозитов')
    rejected_name = 'Отклонённый банк'
    response = client.post(
        f"/api/v1/workflows/{workflow['id']}/run",
        headers=auth,
        json={'inputs': {'records': [{
            'institution_name': rejected_name,
            'product_name': 'Тестовый вклад',
            'currency': 'BYN',
            'rate': '12,5%',
            'term': '3 месяца',
        }]}, 'synchronous': True},
    )
    assert response.status_code == 201, response.text

    task = next(
        item for item in client.get('/api/v1/review', headers=auth).json()
        if item['new_data'].get('institution_name') == rejected_name
    )
    response = client.post(f"/api/v1/review/{task['id']}/reject", headers=auth, json={'comment': 'not verified'})
    assert response.status_code == 200, response.text

    dataset = next(item for item in client.get('/api/v1/datasets', headers=auth).json() if item['slug'] == 'demo-deposits')
    records = client.get(f"/api/v1/datasets/{dataset['id']}/records", headers=auth).json()['items']
    assert all(item['data'].get('institution_name') != rejected_name for item in records)
    exported = client.post(f"/api/v1/exports?dataset_id={dataset['id']}&format=json", headers=auth)
    assert exported.status_code == 200
    assert rejected_name not in exported.text
