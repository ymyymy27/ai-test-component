import {test,expect,type Page} from "@playwright/test";
import {resolve} from "node:path";
import {postHostMessage} from "./host-outcome";
const sent=(page:Page)=>page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1)!);
async function mount(page:Page){
  await page.setViewportSize({width:280,height:800});
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.evaluate(()=>{const host=window as unknown as {__sent:unknown[];acquireVsCodeApi:unknown};host.__sent=[];host.acquireVsCodeApi=()=>({postMessage:(message:unknown)=>host.__sent.push(message)});});
  await page.addScriptTag({path:resolve("dist/index.js")});
  await postHostMessage(page,{kind:"projectContext",project_id:"p",run_id:"r",context_id:"ctx"});
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{result:{supported_actions:["read_view","prepare_execution","authorize_step","execute_step","verify_pending","create_report"],transport_capabilities:["native-confirmation/1"]}}});
  // 面板检测到既有运行会自动读取一次（#108）；先按真实回执闭合这次请求。
  await view(page,"attempt-actual","snapshot-actual");
  await page.getByRole("button",{name:"读取运行详情",exact:true}).click();
  await view(page,"attempt-actual","snapshot-actual");
  await page.getByRole("button",{name:"步骤 1 · 待处理",exact:true}).click();
}
async function view(page:Page,attempt:string,snapshot:string){
  await postHostMessage(page,{kind:"outcome",action:"read_view",status:"ok",response:{result:{view_type:"run",commit_id:1,event_cursor:"cursor",view:{project_id:"p",run_id:"r",snapshot_commit_id:snapshot,selected_case_ids:["case"],required_case_ids:["case"],steps:[{case_id:"case",step_id:"step-actual",state:"pending",current_attempt_id:attempt}]}}}});
}

test("novice run carries exact identities, never auto executes and retains retry intent",async({page})=>{
  await mount(page);
  await expect(page.getByRole("textbox",{name:/JSON|编号|意图|修订/})).toHaveCount(0);
  await page.getByRole("button",{name:"查看将执行的操作",exact:true}).click();
  const prepared=await sent(page);
  expect(prepared).toMatchObject({action:"prepare_execution",target:"step-actual",parameters:{run_id:"r",step_id:"step-actual"}});
  await postHostMessage(page,{kind:"outcome",action:"prepare_execution",status:"ok",response:{result:{execution_action_id:"action-saved"}}});
  expect(await sent(page)).toEqual(prepared);
  await page.getByRole("button",{name:"查看并确认操作授权",exact:true}).click();
  const grant=await sent(page);expect(grant.intentId).toBe(prepared.intentId);
  await postHostMessage(page,{kind:"outcome",action:"authorize_step",status:"refused",code:"USER_DECLINED"});
  await expect(page.getByRole("button",{name:"执行已确认的操作",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"查看并确认操作授权",exact:true}).click();
  expect((await sent(page)).intentId).toBe(grant.intentId);
  await postHostMessage(page,{kind:"outcome",action:"authorize_step",status:"ok",response:{result:{authorization_id:"grant",step_id:"step-actual",intent_id:grant.intentId}}});
  expect((await sent(page)).action).toBe("authorize_step");
  await page.getByRole("button",{name:"执行已确认的操作",exact:true}).click();
  expect(await sent(page)).toMatchObject({intentId:prepared.intentId,parameters:{execution_action_id:"action-saved",record_revision:1}});
  await postHostMessage(page,{kind:"outcome",action:"execute_step",status:"ok",response:{result:{}}});
  await expect(page.getByRole("button",{name:"核验这次执行结果",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"刷新步骤与结果",exact:true}).click();
  await postHostMessage(page,{kind:"outcome",action:"read_view",status:"ok",response:{result:{view_type:"run",commit_id:2,event_cursor:"foreign",view:{project_id:"other",run_id:"r"}}}});
  await expect(page.getByRole("button",{name:"核验这次执行结果",exact:true})).toHaveCount(0);
  await page.getByRole("button",{name:"刷新步骤与结果",exact:true}).click();
  await view(page,"new-attempt","new-snapshot");
  await page.getByRole("button",{name:"核验这次执行结果",exact:true}).click();
  expect(await sent(page)).toMatchObject({action:"verify_pending",parameters:{run_id:"r",step_id:"step-actual",attempt_id:"new-attempt",base_snapshot_commit_id:"new-snapshot"}});
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=window.innerWidth)).toBe(true);
});

test("normal navigation hides technical forms and preserves fold choices",async({page})=>{
  await mount(page);
  await page.getByRole("button",{name:"返回本次测试",exact:true}).click();
  await page.getByText("测试计划",{exact:true}).click();
  await page.getByRole("button",{name:"设置",exact:true}).click();
  await expect(page.getByRole("textbox",{name:"受控操作 JSON"})).toHaveCount(0);
  await page.getByRole("button",{name:"返回本次测试",exact:true}).click();
  await expect(page.getByRole("button",{name:"打开计划与用例",exact:true})).not.toBeVisible();
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await expect(page.getByRole("textbox",{name:"受控操作 JSON"})).toBeVisible();
});
