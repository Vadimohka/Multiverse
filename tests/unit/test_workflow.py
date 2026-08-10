import asyncio

from app.routers.workflows import determine_run_status, stable_record_hash
from workflow_engine import WorkflowEngine, validate_dag
from workflow_engine.types import ExecutionContext


def test_dag_validation_cycle():
    graph={'nodes':[{'id':'a'},{'id':'b'}],'edges':[{'source':'a','target':'b'},{'source':'b','target':'a'}]}
    assert validate_dag(graph)==['Workflow содержит цикл']

def test_dag_execution():
    graph={'nodes':[{'id':'a','type':'manual_trigger','config':{}},{'id':'b','type':'transform','config':{'input_path':'data.records','operations':[{'type':'number','field':'rate'}]}},{'id':'c','type':'output','config':{}}],'edges':[{'source':'a','target':'b'},{'source':'b','target':'c'}]}
    ctx=ExecutionContext(run_id='1',project_id='p',workflow_version_id='1')
    result=asyncio.run(WorkflowEngine().execute(graph,ctx,{'records':[{'rate':'12,5%'}]}))
    assert result['result']['records'][0]['rate']==12.5


def test_fetch_timestamp_does_not_create_a_business_revision():
    before = {"record_id": "42", "title": "Release", "fetched_at": "2026-08-10T12:34:56Z"}
    after = {"record_id": "42", "title": "Release", "fetched_at": "2026-08-11T12:34:56Z"}

    assert stable_record_hash(before) == stable_record_hash(after)


def test_blocked_dataset_persistence_is_a_failed_run():
    assert determine_run_status(
        {"nodes": [{"id": "output", "type": "output", "config": {}}]},
        {"result": {"records": [{"id": "1"}]}, "node_outputs": {}},
        {"enabled": True, "blocked": True, "validation_errors": [{"row": 0}]},
    ) == "FAILED"
