import { test, after } from "node:test";
import assert from "node:assert/strict";
import { mkdtemp, mkdir, writeFile, rm } from "node:fs/promises";
import { resolve, join } from "node:path";
import { pathToFileURL } from "node:url";
import { build } from "esbuild";

// Explicit VS Code API/bridge fixtures prove adapter wiring, not Trae compatibility.
const root = resolve("../..");
await mkdir(join(root, ".runtime/tmp"), { recursive: true });
const fixture = await mkdtemp(join(root, ".runtime/tmp/host-"));
const shim = join(fixture, "host.mjs");
await writeFile(shim, `
export const state = {settings:{pythonPath:"configured-python",workspaceRoot:"data-root",projectId:"p",runId:"r"},sent:[],calls:[],disposed:false};
export const workspace = {isTrusted:true,getConfiguration:()=>({get:(key,fallback)=>state.settings[key]??fallback}),
  onDidChangeConfiguration:cb=>{state.configuration=cb;return {dispose:()=>{state.disposed=true}}}};
export const commands = {registerCommand:(name,cb)=>{state.open=cb;return {dispose(){}}}};
export const Uri = {joinPath:(...parts)=>parts.join("/")};
export const ViewColumn = {One:1};
export const window = {showWarningMessage:()=>{},createWebviewPanel:()=>({webview:{asWebviewUri:x=>x,
  onDidReceiveMessage:cb=>{state.receive=cb;return {dispose(){}}},postMessage:x=>state.sent.push(x)},
  onDidDispose:cb=>{state.close=cb},dispose(){}})};
export async function dispatchReadOnly(python,root,request,id) {state.calls.push({python,root,request,id});
  if(state.defer) return await new Promise(resolve=>{state.resolve=resolve});
  return {status:"ok",response:{result:{status:"READY"}}};}
export async function ensureCore(){return {status:"ok",response:{status:"READY"}};}
`);
await build({ entryPoints: ["src/extension.ts"], bundle: true, platform: "node", format: "esm",
  outfile: join(fixture, "extension.mjs"), plugins: [{ name: "explicit-host-fixture", setup(builder) {
    builder.onResolve({ filter: /^(vscode|\.\/core-bridge)$/ }, () => ({ path: pathToFileURL(shim).href, external: true }));
  } }] });
const { state } = await import(pathToFileURL(shim).href);
const { activate } = await import(pathToFileURL(join(fixture, "extension.mjs")).href);
activate({ subscriptions: [], extensionUri: "extension-root" });
state.open();
const tick = () => new Promise(resolve => setImmediate(resolve));
after(async () => {
  assert.ok(fixture.startsWith(join(root, ".runtime/tmp/host-")));
  await rm(fixture, { recursive: true, force: true });
});

test("host publishes configured scope and preserves request correlation", async () => {
  state.receive({kind:"dispatch",action:"read_view",projectId:"p",parameters:{view:"panel",run_id:"r"},clientRequestId:"request-1"});
  await tick();
  const context = state.sent.find(x=>x.kind==="projectContext");
  assert.equal(context.project_id,"p"); assert.equal(context.run_id,"r");
  assert.ok(context.context_id);
  assert.deepEqual(state.calls.at(-1),{python:"configured-python",root:"data-root",
    request:{action:"read_view",projectId:"p",parameters:{view:"panel",run_id:"r"}},id:"request-1"});
  assert.equal(state.sent.at(-1).clientRequestId,"request-1");
});
test("foreign project cannot reach the bridge", async () => {
  const count=state.calls.length;
  state.receive({kind:"dispatch",action:"query",projectId:"other",parameters:{},clientRequestId:"request-2"});
  await tick();
  assert.equal(state.calls.length,count);
  assert.equal(state.sent.at(-1).code,"PROJECT_CONTEXT_MISMATCH");
});
test("configuration changes reject the in-flight receipt and publish fresh context", async () => {
  state.defer=true;
  state.receive({kind:"dispatch",action:"query",projectId:"p",parameters:{},clientRequestId:"request-3"});
  await tick();
  const previous=state.sent.at(-1).context_id;
  state.settings.projectId="new-project";
  state.configuration({affectsConfiguration:()=>true});
  assert.equal(state.sent.at(-1).project_id,"new-project");
  assert.notEqual(state.sent.at(-1).context_id,previous);
  state.resolve({status:"ok",response:{result:{items:[]}}});
  await tick();
  assert.equal(state.sent.at(-1).status,"unavailable");
  assert.equal(state.sent.at(-1).code,"PROJECT_CONTEXT_CHANGED");
  assert.equal(state.sent.at(-1).clientRequestId,"request-3");
  state.defer=false;
});
test("missing project blocks scoped reads and panel disposal releases configuration watch", async () => {
  state.settings.projectId="";
  const count=state.calls.length;
  state.receive({kind:"dispatch",action:"read_view",parameters:{view:"panel",run_id:"r"},clientRequestId:"request-4"});
  await tick();
  assert.equal(state.calls.length,count);
  assert.equal(state.sent.findLast(x=>x.kind==="projectContext").project_id,"");
  assert.equal(state.sent.findLast(x=>x.kind==="projectContext").run_id,null);
  state.close(); assert.equal(state.disposed,true);
});
