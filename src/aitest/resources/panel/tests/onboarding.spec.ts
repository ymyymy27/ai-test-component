import {test,expect,type Page} from "@playwright/test";
import {resolve} from "node:path";
import {postHostMessage} from "./host-outcome";
const sent=(page:Page)=>page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1)!);
async function wizardReply(page:Page,value:Record<string,unknown>){
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:(await sent(page)).clientRequestId,...value});
}
test("empty project wizard preserves cancelled intent and sequences commands after pending clears",async({page})=>{
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.evaluate(()=>{const host=window as unknown as {__sent:unknown[];acquireVsCodeApi:unknown};host.__sent=[];host.acquireVsCodeApi=()=>({postMessage:(message:unknown)=>host.__sent.push(message)});});
  await page.addScriptTag({path:resolve("dist/index.js")});
  await postHostMessage(page,{kind:"projectContext",project_id:"",context_id:"empty"});
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{workspace_id:"ws",result:{supported_actions:[]}}});
  await page.getByRole("button",{name:"开始第一次测试",exact:true}).click();
  await page.getByRole("button",{name:"选择项目目录",exact:true}).click();
  await wizardReply(page,{session:{project_id:"guided-project",name:"示例",source:"E:/sample",entry:"main.py",reference:"safe"}});
  await page.getByRole("textbox",{name:"你希望验证什么",exact:true}).fill("核对加法");
  await page.getByRole("textbox",{name:"测试数据如何隔离、测试后如何复位",exact:true}).fill("纯计算无需复位");
  await page.getByRole("checkbox",{name:"我确认这是本地测试入口，不会修改真实业务数据"}).check();
  await page.getByRole("button",{name:"下一步：模型 API",exact:true}).click();
  await page.getByRole("checkbox",{name:"使用模型帮助准备检查建议"}).uncheck();
  await page.getByRole("button",{name:"准备环境并继续",exact:true}).click();
  await postHostMessage(page,{kind:"projectContext",project_id:"guided-project",context_id:"new"});
  expect((await sent(page)).kind).toBe("wizardSetup");
  await wizardReply(page,{setup:{}});
  expect((await sent(page)).action).toBe("doctor");
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{workspace_id:"ws-new",result:{supported_actions:["save_context","save_binding"]}}});
  await page.getByRole("button",{name:"保存项目并继续",exact:true}).click();
  expect((await sent(page)).form).toMatchObject({workspace_id:"ws-new"});
  const commands=["save_context","save_binding"].map((action,index)=>({key:action,action,intent_id:"stable-"+index,expected_revision:0,binding_revision:1,parameters:{}}));
  await wizardReply(page,{commands});
  expect((await sent(page)).action).toBe("save_context");
  await postHostMessage(page,{kind:"outcome",action:"save_context",status:"ok",response:{result:{saved:true}}});
  expect((await sent(page)).action).toBe("save_binding");
  await postHostMessage(page,{kind:"outcome",action:"save_binding",status:"refused",code:"USER_DECLINED"});
  await expect(page.getByRole("button",{name:"保存项目并继续",exact:true})).toBeDisabled();
  await page.getByRole("button",{name:"继续当前步骤",exact:true}).click();
  expect((await sent(page)).intentId).toBe("stable-1");
  await postHostMessage(page,{kind:"outcome",action:"save_binding",status:"ok",response:{result:{saved:true}}});
  await expect(page.getByRole("heading",{name:"核对检查计划",exact:true})).toBeVisible();
  await expect(page.getByRole("textbox",{name:/JSON|编号|意图|修订/})).toHaveCount(0);
});

test("AI proposal review publishes a full plan and keeps human confirmation",async({page})=>{
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.evaluate(()=>{const host=window as unknown as {__sent:unknown[];acquireVsCodeApi:unknown};host.__sent=[];host.acquireVsCodeApi=()=>({postMessage:(message:unknown)=>host.__sent.push(message)});});
  await page.addScriptTag({path:resolve("dist/index.js")});
  const supported=["save_context","save_binding","save_environment","analyze_project","save_model_outbound_policy","generate_draft","save_case","save_acceptance","publish_plan","prepare_run","register_run"];
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{workspace_id:"ws",result:{supported_actions:supported}}});
  await page.getByRole("button",{name:"开始第一次测试",exact:true}).click();
  await page.getByRole("button",{name:"选择项目目录",exact:true}).click();
  await wizardReply(page,{session:{project_id:"guided-ai",name:"示例",source:"E:/sample",entry:"main.py",reference:"safe"}});
  await page.getByRole("textbox",{name:"你希望验证什么",exact:true}).fill("核对加法");
  await page.getByRole("textbox",{name:"测试数据如何隔离、测试后如何复位",exact:true}).fill("纯计算无需复位");
  await page.getByRole("checkbox",{name:"我确认这是本地测试入口，不会修改真实业务数据"}).check();
  await page.getByRole("button",{name:"下一步：模型 API",exact:true}).click();
  await page.getByRole("button",{name:"准备环境并继续",exact:true}).click();
  await wizardReply(page,{setup:{}});
  await expect.poll(async()=> (await sent(page)).action).toBe("doctor");
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{workspace_id:"ws-ai",result:{supported_actions:supported}}});
  await page.getByRole("button",{name:"保存项目并继续",exact:true}).click();
  await wizardReply(page,{commands:[
    {key:"context",action:"save_context",intent_id:"i1",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"binding",action:"save_binding",intent_id:"i2",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"environment",action:"save_environment",intent_id:"i3",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"snapshot",action:"analyze_project",intent_id:"i4",expected_revision:0,binding_revision:1,parameters:{}},
  ]});
  for(const action of ["save_context","save_binding","save_environment"]){
    await expect.poll(async()=> (await sent(page)).action).toBe(action);
    await postHostMessage(page,{kind:"outcome",action,status:"ok",response:{result:{saved:true}}});
  }
  await expect.poll(async()=> (await sent(page)).action).toBe("analyze_project");
  const understanding={schema_version:"aitest.project-understanding/1",understanding_id:"u",revision:1};
  await postHostMessage(page,{kind:"outcome",action:"analyze_project",status:"ok",response:{result:{snapshot_id:"snap",understanding}}});
  await page.getByRole("button",{name:"生成 AI 计划",exact:true}).click();
  const modelRequest=await sent(page);
  expect(modelRequest.form).toMatchObject({understanding,run_tier:"full",initial_driver:"planned"});
  await wizardReply(page,{commands:[
    {key:"policy",action:"save_model_outbound_policy",intent_id:"i5",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"suggestions",action:"generate_draft",intent_id:"i6",expected_revision:0,binding_revision:1,parameters:{}},
  ]});
  await expect.poll(async()=> (await sent(page)).action).toBe("save_model_outbound_policy");
  await postHostMessage(page,{kind:"outcome",action:"save_model_outbound_policy",status:"ok",response:{result:{saved:true}}});
  await expect.poll(async()=> (await sent(page)).action).toBe("generate_draft");
  const proposal={schema_version:"aitest.plan-proposal/1.1",goal:"核对加法",cases:[{case_id:"case-1"}]};
  await postHostMessage(page,{kind:"outcome",action:"generate_draft",status:"ok",response:{result:{draft_text:"{}",proposal}}});
  await expect.poll(async()=> (await sent(page)).kind).toBe("wizardReview");
  const review=await sent(page);
  expect(review.form).toMatchObject({proposal,understanding});
  await wizardReply(page,{valid:true,preview:{coverage:{template_items:1,covered_items:1,required_items:1},coverage_contract:{contract_id:"c"},understanding_ref:{understanding_id:"u",revision:1}}});
  await expect(page.getByRole("heading",{name:"核对 AI 计划",exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"确认并发布",exact:true})).toBeDisabled();
  await page.getByRole("checkbox",{name:/业务功能目录完整/}).check();
  await expect(page.getByRole("button",{name:"确认并发布",exact:true})).toBeEnabled();
  await page.getByRole("button",{name:"确认并发布",exact:true}).click();
  const commit=await sent(page);
  expect(commit.kind).toBe("wizardCommands");expect(commit.stage).toBe("commit");
  expect(commit.form).toMatchObject({proposal,run_tier:"full",catalog_confirmed:true,
    coverage_contract:{contract_id:"c"},understanding_ref:{understanding_id:"u",revision:1}});
  await wizardReply(page,{commands:[
    {key:"case-case-1",action:"save_case",intent_id:"i7",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"scope",action:"save_acceptance",intent_id:"i8",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"publish",action:"publish_plan",intent_id:"i9",expected_revision:0,binding_revision:1,parameters:{}},
  ]});
  for(const action of ["save_case","save_acceptance","publish_plan"]){
    await expect.poll(async()=> (await sent(page)).action).toBe(action);
    await postHostMessage(page,{kind:"outcome",action,status:"ok",response:{result:{plan_confirmation_id:"pc",basis_confirmations:[{confirmation_id:"c",case_id:"case-1",basis_revision:1,confirmed_at_commit:1}]}}});
  }
  await expect.poll(async()=> (await sent(page)).stage).toBe("prepare");
  await wizardReply(page,{commands:[{key:"prepare",action:"prepare_run",intent_id:"i10",expected_revision:0,binding_revision:1,parameters:{}}]});
  await expect.poll(async()=> (await sent(page)).action).toBe("prepare_run");
  await postHostMessage(page,{kind:"outcome",action:"prepare_run",status:"ok",response:{result:{prepared_run_id:"p"}}});
  await expect.poll(async()=> (await sent(page)).stage).toBe("register");
  await wizardReply(page,{commands:[{key:"register",action:"register_run",intent_id:"i11",expected_revision:0,binding_revision:1,parameters:{}}]});
  await expect.poll(async()=> (await sent(page)).action).toBe("register_run");
  await postHostMessage(page,{kind:"outcome",action:"register_run",status:"ok",response:{result:{run_id:"run"}}});
  await expect(page.getByRole("button",{name:"进入测试工作台",exact:true})).toBeVisible();
});
