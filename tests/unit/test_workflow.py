import asyncio

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
