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
  await page.getByRole("button",{name:"下一步：模型 API",exact:true}).click();
  await page.getByRole("checkbox",{name:"使用模型帮助准备检查建议"}).uncheck();
  await page.getByRole("button",{name:"下一步：测试环境",exact:true}).click();
  await page.getByRole("textbox",{name:"测试数据如何隔离、测试后如何复位",exact:true}).fill("纯计算无需复位");
  await page.getByRole("checkbox",{name:"我确认这是本地测试入口，不会修改真实业务数据"}).check();
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

test("review shows project coverage and commit is one plan confirmation",async({page})=>{
  await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.evaluate(()=>{const host=window as unknown as {__sent:unknown[];acquireVsCodeApi:unknown};host.__sent=[];host.acquireVsCodeApi=()=>({postMessage:(message:unknown)=>host.__sent.push(message)});});
  await page.addScriptTag({path:resolve("dist/index.js")});
  await postHostMessage(page,{kind:"projectContext",project_id:"",context_id:"empty"});
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{workspace_id:"ws",result:{supported_actions:[]}}});
  await page.getByRole("button",{name:"开始第一次测试",exact:true}).click();
  await page.getByRole("button",{name:"选择项目目录",exact:true}).click();
  await wizardReply(page,{session:{project_id:"guided-project",name:"示例",source:"E:/sample",entry:"main.py",reference:"safe"}});
  await page.getByRole("textbox",{name:"你希望验证什么",exact:true}).fill("核对加法");
  await page.getByRole("button",{name:"下一步：模型 API",exact:true}).click();
  await page.getByRole("button",{name:"下一步：测试环境",exact:true}).click();
  await page.getByRole("textbox",{name:"测试数据如何隔离、测试后如何复位",exact:true}).fill("纯计算无需复位");
  await page.getByRole("checkbox",{name:"我确认这是本地测试入口，不会修改真实业务数据"}).check();
  await page.getByRole("button",{name:"准备环境并继续",exact:true}).click();
  await postHostMessage(page,{kind:"projectContext",project_id:"guided-project",context_id:"new"});
  await wizardReply(page,{setup:{}});
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{workspace_id:"ws-new",result:{supported_actions:["save_context","save_binding"]}}});
  await page.getByRole("button",{name:"保存项目并继续",exact:true}).click();
  const projectCommands=[
    {key:"project",action:"save_context",intent_id:"i1",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"binding",action:"save_binding",intent_id:"i2",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"environment",action:"save_environment",intent_id:"i3",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"snapshot",action:"analyze_project",intent_id:"i4",expected_revision:0,binding_revision:1,parameters:{}},
  ];
  await wizardReply(page,{commands:projectCommands});
  for(const action of ["save_context","save_binding","save_environment","analyze_project"]){
    await expect.poll(async()=> (await sent(page)).action).toBe(action);
    const response=action==="analyze_project"
      ? {result:{snapshot_id:"s1",understanding:{understanding_id:"u1",revision:1}}}
      : {result:{saved:true}};
    await postHostMessage(page,{kind:"outcome",action,status:"ok",response});
  }
  await expect(page.getByRole("heading",{name:"确认模型使用范围",exact:true})).toBeVisible();
  await page.getByRole("button",{name:"确认发送并生成检查建议",exact:true}).click();
  await expect.poll(async()=> (await sent(page)).stage).toBe("model");
  const proposal={schema_version:"aitest.plan-proposal/1.1",template_id:"python-library",template_version:"1.0.0",
    scope:{name:"n",objective:"o",required_case_ids:[],excluded_case_ids:[],exclusion_reasons:[],dependency_closure_ids:[],applicability_exclusions:[]},
    cases:[{case_id:"case-normal",template_item_id:"normal",layer:"L2",objective:"o",preconditions:[],inputs:[],steps:[],expected:"e",verification_method:"v",links:{acceptance_item_ids:["normal"],module_ids:[],critical_path_ids:[],no_critical_path_reason:"r"},assertion_basis_text:"b",independent_verification:"v",mock_scope:[],importance:"P1"}],
    understanding_revision:1,goal_intent_ids:["intent-1"],coverage_claims:[]};
  const preview={proposal,scope:proposal.scope,cases:proposal.cases,
    coverage:{template_items:4,covered_items:1,required_items:1},
    coverage_preview:[{function_id:"fn-add",overall_status:"not_proven",dimensions:{normal:"planned"},gap_reason:"no coverage claim"}],
    coverage_contract:{contract_id:"c1",understanding_revision:1},
    understanding_ref:{understanding_id:"u1",revision:1}};
  await wizardReply(page,{commands:[
    {key:"policy",action:"save_model_outbound_policy",intent_id:"i5",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"suggestions",action:"generate_draft",intent_id:"i6",expected_revision:0,binding_revision:1,parameters:{}},
  ]});
  await expect.poll(async()=> (await sent(page)).action).toBe("save_model_outbound_policy");
  await postHostMessage(page,{kind:"outcome",action:"save_model_outbound_policy",status:"ok",response:{result:{saved:true}}});
  await expect.poll(async()=> (await sent(page)).action).toBe("generate_draft");
  await postHostMessage(page,{kind:"outcome",action:"generate_draft",status:"ok",response:{result:{proposal,draft_text:""}}});
  await expect.poll(async()=> (await sent(page)).kind).toBe("wizardReview");
  await wizardReply(page,{preview});
  await expect(page.getByTestId("plan-coverage")).toBeVisible();
  await expect(page.getByText("fn-add")).toBeVisible();
  await page.getByRole("button",{name:"校验并发布",exact:true}).click();
  await expect.poll(async()=> (await sent(page)).kind).toBe("wizardReview");
  await wizardReply(page,{preview});
  await expect.poll(async()=> (await sent(page)).stage).toBe("commit");
  const commitCommands=[
    {key:"case-case-normal",action:"save_case",intent_id:"i7",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"scope",action:"save_acceptance",intent_id:"i8",expected_revision:0,binding_revision:1,parameters:{}},
    {key:"publish",action:"publish_plan",intent_id:"i9",expected_revision:0,binding_revision:1,parameters:{plan_grant:{allowed_action_classes:["unknown"]}}},
  ];
  expect(commitCommands.some(item=>item.action==="confirm_basis")).toBe(false);
  await wizardReply(page,{commands:commitCommands});
  await expect.poll(async()=> (await sent(page)).action).toBe("save_case");
});
