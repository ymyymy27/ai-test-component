import {test,after} from "node:test";
import assert from "node:assert/strict";
import {mkdtemp,mkdir,writeFile,rm} from "node:fs/promises";
import {resolve,join} from "node:path";
import {pathToFileURL} from "node:url";
import {build} from "esbuild";

const base=resolve("../../.runtime/tmp");await mkdir(base,{recursive:true});
const root=await mkdtemp(join(base,"onboarding-host-"));
const shim=join(root,"shim.mjs");
await writeFile(shim,`
import {EventEmitter} from 'node:events';
export const state={requests:[],key:'fixture-only',fail:false};
export const window={showInputBox:async()=>state.key};
export function spawn(){const child=new EventEmitter();child.stdout=new EventEmitter();child.stderr={resume(){}};child.kill=()=>{};
 child.stdin=new EventEmitter();child.stdin.end=value=>{const request=JSON.parse(value);state.requests.push(request);
 queueMicrotask(()=>{child.stdout.emit('data',Buffer.from(JSON.stringify({ok:!state.fail,result:{configured:true,model_file:'fixture/model.json'}})));child.emit('close',state.fail?2:0);});};return child;}
`);
await build({entryPoints:["src/onboarding.ts"],bundle:true,platform:"node",format:"esm",outfile:join(root,"subject.mjs"),plugins:[{name:"host-fixture",setup(b){b.onResolve({filter:/^(vscode|node:child_process)$/},()=>({path:pathToFileURL(shim).href,external:true}));}}]});
const {wizardMessage}=await import(pathToFileURL(join(root,"subject.mjs")));
const {state}=await import(pathToFileURL(shim));
after(async()=>{assert.ok(root.startsWith(join(base,"onboarding-host-")));await rm(root,{recursive:true,force:true});});

test("model defaults save only after public configuration and vault validation succeed",async()=>{
 const values=new Map(),responses=[];
 const context={globalStorageUri:{fsPath:root},globalState:{get:key=>values.get(key),update:async(key,value)=>values.set(key,value)}};
 const settings={python:"fixture",modelConfigurationFile:"",workspaceRoot:"",projectId:null};
 await wizardMessage(context,{kind:"wizardModelCredential",clientRequestId:"credential"},undefined,settings,x=>responses.push(x));
 const reference=responses.at(-1).modelCredential;
 assert.ok(reference.startsWith("model-"));
 assert.equal(JSON.stringify(responses).includes("fixture-only"),false);
 assert.equal(state.requests.at(-1).operation,"credential");
 const request={kind:"wizardModelSave",clientRequestId:"save",endpoint:"https://api.example",model:"model",reference,timeout:60};
 state.fail=true;
 await wizardMessage(context,request,undefined,settings,x=>responses.push(x));
 assert.equal(values.has("modelDefaults"),false);assert.ok(responses.at(-1).error);
 state.fail=false;
 await wizardMessage(context,request,undefined,settings,x=>responses.push(x));
 assert.equal(responses.at(-1).modelSaved,true);
 assert.deepEqual(values.get("modelDefaults"),{endpoint:request.endpoint,model:"model",reference,timeout:60});
 const count=state.requests.length;
 await wizardMessage(context,{...request,timeout:121},undefined,settings,x=>responses.push(x));
 assert.equal(state.requests.length,count);assert.ok(responses.at(-1).error);
 await wizardMessage(context,{kind:"wizardModelLoad",clientRequestId:"load"},undefined,settings,x=>responses.push(x));
 assert.equal(responses.at(-1).modelSettings.reference,reference);
 assert.equal(responses.at(-1).clientRequestId,"load");
});
