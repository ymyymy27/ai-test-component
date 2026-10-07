from pathlib import Path
import sys, zipfile, json, tempfile, asyncio
root=Path.cwd()
site=root/'.git/p1-abc-fix-20261003/mcp-read-wheel-site'
wheel=root/'.git/p1-abc-fix-20261003/mcp-read-dist/ai_test_component-0.4.0-py3-none-any.whl'
with zipfile.ZipFile(wheel) as archive:
    for relative in ('interfaces/tools/agent_relay.py','interfaces/tools/cli.py'):
        member='aitest/'+relative
        assert archive.read(member)==(root/'src'/member).read_bytes(),member
    archive.extractall(site)
sys.path.insert(0,str(site))
from aitest.bootstrap import acquire_endpoint,shutdown_endpoint
from aitest.infrastructure.file_store.unit_of_work import FileUnitOfWork
# Only this client process imports external SDK dependencies; server/core keep locked venv.
sys.path.insert(0,str(root/'.git/p1-abc-fix-20261003/mcp-client-sdk'))
from mcp import Client, StdioServerParameters
from importlib.metadata import version
assert version('mcp')=='2.3.0'
code="import sys;sys.path.insert(0,sys.argv.pop(1));from aitest.interfaces.tools.cli import main;raise SystemExit(main())"
async def check(data, endpoint):
    params=StdioServerParameters(command=sys.executable,args=['-I','-c',code,str(site),'mcp-relay','--workspace',str(data),'--project','project','--binding','binding'])
    async with Client(params) as client:
        assert client.protocol_version in {'2024-11-05','2025-03-26','2025-06-18','2025-11-25'}
        tools=await client.list_tools()
        assert {tool.name for tool in tools.tools}=={'aitest_doctor','aitest_query'}
        for name,args in [('aitest_doctor',{}),('aitest_query',{'aggregate_kind':'binding','record_id':'binding','limit':1})]:
            result=await client.call_tool(name,args)
            assert result.is_error is False
            response=result.structured_content or json.loads(result.content[0].text)
            assert response['instance_id']==endpoint.instance_id
            assert response['workspace_id']==endpoint.workspace_id
            assert response['project_id']=='project' and response['binding_revision']==1
            if name=='aitest_query':assert response['result']['items'][0]['record_id']=='binding'
        print('Official mcp SDK 2.3.0 actual stdio handshake/list/call:',client.protocol_version,'; one verified Windows core, project/binding exact.')
with tempfile.TemporaryDirectory(prefix='aitest-mcp-wheel-') as directory:
    data=Path(directory)/'data'
    raw=FileUnitOfWork(data);raw.begin('seed','project')
    for kind,identity in [('project','project'),('binding','binding')]:
        raw.stage_record(aggregate_kind=kind,record_id=identity,expected_revision=0,payload={'project_id':'project',**({'binding_id':'binding'} if kind=='binding' else {})})
    raw.commit()
    endpoint=acquire_endpoint(data);endpoint.connection.close()
    before=(data/'workspace.json').read_bytes()
    try:
        asyncio.run(asyncio.wait_for(check(data,endpoint),timeout=50))
        assert (data/'workspace.json').read_bytes()==before
    finally:shutdown_endpoint(data)
print('Installed two changed modules match final source. Synthetic selected binding metadata only; not real Trae/confirmed source/AC.')
