import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

// Deliberate CLI process fixture, not a real core or Trae acceptance environment.
const root = resolve("../..");
await mkdir(join(root, ".runtime/tmp"), { recursive: true });
const fixture = await mkdtemp(join(root, ".runtime/tmp/bridge-"));
const modulePath = join(fixture, "aitest/interfaces/tools");
await mkdir(modulePath, { recursive: true });
for (const part of ["aitest", "aitest/interfaces", "aitest/interfaces/tools"]) {
  await writeFile(join(fixture, part, "__init__.py"), "");
}
await writeFile(join(modulePath, "cli.py"), String.raw`
import sys, json, time, os
mode = sys.argv[-1]
if mode == 'timeout':
    time.sleep(10)
elif mode in ('large_stdout', 'large_stderr'):
    stream = sys.stdout if mode == 'large_stdout' else sys.stderr
    stream.write('汉' * (3 * 1024 * 1024))
    stream.flush()
elif mode == 'secret_stderr':
    sys.stderr.write('fixture-credential-must-never-echo')
elif mode == 'secret_code_stderr':
    sys.stderr.write(json.dumps(dict(code='fixture-credential-must-never-echo')))
elif mode == 'array':
    print('[]')
elif sys.argv[1] == 'ensure-core':
    data = dict(status='READY', workspace_id='w', instance_id='i')
    if mode == 'carrier_config': data['carrier_config'] = os.environ.get('AITEST_PYTHON_CARRIERS')
    if mode == 'missing_identity': del data['instance_id']
    print(json.dumps(data))
    if mode == 'exit': sys.exit(1)
else:
    command = json.load(sys.stdin)
    data = dict(protocol_version='aitest.local/2.0', request_id=command['request_id'],
                project_id=command.get('project_id'), workspace_id='w', instance_id='i',
                intent_id=command.get('intent_id'), binding_revision=command.get('binding_revision'),
                result=dict(status='READY'), error=None)
    if mode == 'request': data['request_id'] = 'other'
    if mode == 'protocol': data['protocol_version'] = 'unknown'
    if mode == 'project': data['project_id'] = 'other'
    if mode == 'intent': data['intent_id'] = 'other'
    if mode == 'binding': data['binding_revision'] = 99
    if mode == 'missing_identity': del data['instance_id']
    if mode == 'result_array': data['result'] = []
    if mode == 'error_array': data['error'] = []
    if mode == 'refused': data['error'] = dict(code='FIXTURE_REFUSED', message='refused')
    print(json.dumps(data))
    if mode == 'exit': sys.exit(1)
`);
await build({ entryPoints: ["src/core-bridge.ts"], bundle: true, platform: "node", format: "esm",
  outfile: join(fixture, "bridge.mjs") });
const { dispatchReadOnly, dispatchControlled, buildCommand, ensureCore } = await import(pathToFileURL(join(fixture, "bridge.mjs")).href);
const originalPath = process.env.PYTHONPATH;
process.env.PYTHONPATH = fixture;
const python = process.env.AITEST_TEST_PYTHON || join(root, ".venv/Scripts/python.exe");
after(async () => {
  if (originalPath === undefined) delete process.env.PYTHONPATH;
  else process.env.PYTHONPATH = originalPath;
  // Exact test-created directory under the checked workspace prefix.
  assert.ok(fixture.startsWith(join(root, ".runtime/tmp/bridge-")));
  await rm(fixture, { recursive: true, force: true });
});

test("only exact successful receipts are accepted", async () => {
  const outcome = await dispatchReadOnly(python, "ok", { action: "doctor", projectId: "p" }, "r", 2000);
  assert.equal(outcome.status, "ok");
});
test("host carrier configuration is passed only as startup configuration", async () => {
  const before = process.env.AITEST_PYTHON_CARRIERS;
  const outcome = await ensureCore(python, "carrier_config", 5000, "C:/trusted/carriers.json");
  assert.equal(outcome.status, "ok");
  assert.equal(outcome.response.carrier_config, "C:/trusted/carriers.json");
  assert.equal(process.env.AITEST_PYTHON_CARRIERS, before);
});
for (const mode of ["request", "protocol", "project", "missing_identity", "exit", "array", "result_array", "error_array"]) {
  test(`unverified ${mode} receipt is refused as success`, async () => {
    const outcome = await dispatchReadOnly(python, mode, { action: "doctor", projectId: "p" }, "r", 2000);
    assert.equal(outcome.status, "unavailable");
    assert.equal(outcome.code, "CORE_RESULT_UNVERIFIED");
  });
}
for (const mode of ["large_stdout", "large_stderr"]) {
  test(`${mode} is limited by UTF8 bytes`, async () => {
    const outcome = await dispatchReadOnly(python, mode, { action: "doctor" }, "r", 5000);
    assert.equal(outcome.code, "ENTRY_OUTPUT_LIMIT");
    const startup = await ensureCore(python, mode, 5000);
    assert.equal(startup.code, "ENTRY_OUTPUT_LIMIT");
  });
}
test("raw stderr is never copied into the outcome", async () => {
  const outcome = await dispatchReadOnly(python, "secret_stderr", { action: "doctor" }, "r", 2000);
  assert.equal(outcome.status, "unavailable");
  assert.ok(!JSON.stringify(outcome).includes("fixture-credential"));
});
test("startup refusal cannot copy untrusted stderr code into its outcome", async () => {
  const outcome = await ensureCore(python, "secret_code_stderr", 2000);
  assert.equal(outcome.status, "refused");
  assert.ok(!JSON.stringify(outcome).includes("fixture-credential"));
});
test("timeout cannot become success", async () => {
  assert.equal((await dispatchReadOnly(python, "timeout", { action: "doctor" }, "r", 150)).code, "ENTRY_TIMEOUT");
  assert.equal((await ensureCore(python, "timeout", 150)).code, "ENTRY_TIMEOUT");
});
test("ensure requires zero exit and complete core identity", async () => {
  assert.equal((await ensureCore(python, "ok", 2000)).status, "ok");
  assert.equal((await ensureCore(python, "exit", 2000)).status, "refused");
  assert.equal((await ensureCore(python, "missing_identity", 2000)).status, "refused");
});
test("controlled refusal stays refusal and writes stay forbidden", async () => {
  assert.equal((await dispatchReadOnly(python, "refused", { action: "doctor" }, "r", 2000)).code, "FIXTURE_REFUSED");
  await assert.rejects(dispatchReadOnly(python, "ok", { action: "select_case_reuse" }, "r", 2000));
});

test("controlled bridge preserves exact identities and refuses mismatched receipts", async () => {
  const request={action:"save_binding",projectId:"p",intentId:"stable-intent",expectedRevision:0,
    target:"binding",bindingRevision:1,parameters:{binding:{binding_id:"binding"}}};
  const command=buildCommand(request,"request");
  assert.equal(command.intent_id,"stable-intent"); assert.equal(command.expected_revision,0);
  assert.equal(command.target,"binding"); assert.equal(command.binding_revision,1);
  assert.equal((await dispatchControlled(python,"ok",request,"request",2000)).status,"ok");
  for(const mode of ["intent","binding","project"]){
    assert.equal((await dispatchControlled(python,mode,request,"request",2000)).code,"CORE_RESULT_UNVERIFIED");
  }
  for(const change of [{intentId:null},{intentId:"request"},{expectedRevision:-1},{expectedRevision:true}]){
    await assert.rejects(dispatchControlled(python,"ok",{...request,...change},"request",2000));
  }
});
test("invalid startup configuration and budget fail before spawning", async () => {
  for (const budget of [0, -1, NaN, Infinity, 60001]) {
    assert.equal((await ensureCore(python, "ok", budget)).code, "ENTRY_INVALID_CONFIG");
    assert.equal((await dispatchReadOnly(python, "ok", { action: "doctor" }, "r", budget)).code, "ENTRY_INVALID_CONFIG");
  }
});
