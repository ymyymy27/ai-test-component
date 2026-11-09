import {test,expect,type Page} from "@playwright/test";
import {resolve} from "node:path";
import {postHostMessage} from "./host-outcome";
const sent=(page:Page)=>page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1)!);
async function mount(page:Page){
  await page.setContent('<html lang="zh-CN"><body style="margin:0"><ai-test-panel></ai-test-panel></body></html>');
  await page.evaluate(()=>{const host=window as unknown as {__sent:unknown[];acquireVsCodeApi:unknown};host.__sent=[];host.acquireVsCodeApi=()=>({postMessage:(m:unknown)=>host.__sent.push(m)});});
  await page.addScriptTag({path:resolve("dist/index.js")});
}
test("empty panel offers a single clear onboarding path",async({page})=>{
  await mount(page);
  await expect(page.getByText("未选择项目",{exact:true})).toBeVisible();
  await expect(page.getByRole("button",{name:"新建测试",exact:true})).toBeVisible();
  await expect(page.getByText("选择一个本地 Python 项目",{exact:false})).toBeVisible();
});
test("project and run view render steps without recalculating conclusions",async({page})=>{
  await mount(page);
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{result:{status:"READY",supported_actions:["query","read_view"],model:{status:"已配置"}}}});
  await postHostMessage(page,{kind:"projectContext",project_id:"p",run_id:"r",scope_name:"订单服务回归",context_id:"c"});
  await expect.poll(async()=> (await sent(page)).action).toBe("read_view");
  const request=await sent(page);
  await postHostMessage(page,{kind:"outcome",action:"read_view",clientRequestId:request.clientRequestId,status:"ok",response:{result:{view_type:"run",commit_id:2,event_cursor:"e",view:{project_id:"p",run_id:"r",snapshot_commit_id:"s",control_state:"running",selected_summary:{executed_count:1,verified_count:1,unverified_count:0},steps:[{case_id:"case-1",step_id:"step-1",state:"completed"}]}}}});
  await expect(page.getByText("订单服务回归").first()).toBeVisible();
  await expect(page.getByText("执行",{exact:true})).toBeVisible();
  await page.getByRole("button",{name:"运行",exact:true}).click();
  await expect(page.getByText("case-1",{exact:true})).toBeVisible();
  await expect(page.getByText("已完成",{exact:true}).first()).toBeVisible();
});
test("settings loads explicit model profiles",async({page})=>{
  await mount(page);
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{result:{status:"READY",supported_actions:[]}}});
  await page.getByRole("button",{name:"设置",exact:true}).click();
  await expect.poll(async()=> page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.some(m=>m.kind==="wizardModelLoad"))).toBe(true);
  const request=await page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.find(m=>m.kind==="wizardModelLoad")!);
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:request.clientRequestId,modelSettings:{endpoint:"https://api.example",model:"m",timeout:60,reference:"ref"},modelProfiles:[{profile_id:"p1",name:"默认"}],activeProfileId:"p1"});
  await expect(page.getByRole("option",{name:"默认"})).toBeAttached();
});

test("first-run wizard can complete with one plan publication",async({page})=>{
  await mount(page);
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{result:{status:"READY",workspace_id:"ws",supported_actions:["query","read_view"]}}});
  const all=()=>page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent);
  const lastOf=(predicate:(m:Record<string,unknown>)=>boolean)=>page.evaluate((source)=>{
    const sent=(window as unknown as {__sent:Record<string,unknown>[]}).__sent;
    return [...sent].reverse().find(m=>new Function("m","return "+source)(m)) ?? null;
  }, predicate.toString());
  const reply=async(result:Record<string,unknown>)=>{
    const messages=await all();const request=messages.at(-1)!;
    await postHostMessage(page,{kind:"wizardResult",clientRequestId:request.clientRequestId,...result});
  };
  await page.getByRole("button",{name:"选择项目目录",exact:true}).click();
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardChoose")).toBe(true);
  await reply({session:{project_id:"guided-1",name:"示例",source:"E:/sample",entry:"main.py",reference:"ref"}});
  await page.getByRole("button",{name:"配置密钥",exact:true}).click();
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardModelCredential")).toBe(true);
  await reply({modelCredential:"ref"});
  await page.getByRole("button",{name:"继续",exact:true}).click();
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardSetup")).toBe(true);
  await reply({setup:{workspace:"ws-path"}});
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardCommands"&&m.stage==="project")).toBe(true);
  await page.evaluate(()=>{
    const sent=(window as unknown as {__sent:Record<string,unknown>[]}).__sent;
    const request=[...sent].reverse().find(m=>m.kind==="wizardCommands"&&m.stage==="project")!;
    window.postMessage({kind:"wizardResult",clientRequestId:request.clientRequestId,commands:[
      {key:"project",action:"save_context",intent_id:"i1",expected_revision:0,binding_revision:1,parameters:{}},
      {key:"binding",action:"save_binding",intent_id:"i2",expected_revision:0,binding_revision:1,parameters:{}},
      {key:"environment",action:"save_environment",intent_id:"i3",expected_revision:0,binding_revision:1,parameters:{}},
      {key:"snapshot",action:"analyze_project",intent_id:"i4",expected_revision:0,binding_revision:1,parameters:{}},
    ]},"*");
  });
  for(const action of ["save_context","save_binding","save_environment","analyze_project"]){
    await expect.poll(async()=> (await all()).at(-1)?.action).toBe(action);
    await postHostMessage(page,{kind:"outcome",action,status:"ok",response:{result:{}}});
  }
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardCommands"&&m.stage==="model")).toBe(true);
  await page.evaluate(()=>{
    const sent=(window as unknown as {__sent:Record<string,unknown>[]}).__sent;
    const request=[...sent].reverse().find(m=>m.kind==="wizardCommands"&&m.stage==="model")!;
    window.postMessage({kind:"wizardResult",clientRequestId:request.clientRequestId,commands:[
      {key:"policy",action:"save_model_outbound_policy",intent_id:"i5",expected_revision:0,binding_revision:1,parameters:{}},
      {key:"suggestions",action:"generate_draft",intent_id:"i6",expected_revision:0,binding_revision:1,parameters:{}},
    ]},"*");
  });
  for(const action of ["save_model_outbound_policy","generate_draft"]){
    await expect.poll(async()=> (await all()).at(-1)?.action).toBe(action);
    await postHostMessage(page,{kind:"outcome",action,status:"ok",response:{result:{proposal:{schema_version:"aitest.plan-proposal/1.1"}}}});
  }
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardReview")).toBe(true);
  const review=await page.evaluate(()=>[...(window as unknown as {__sent:Record<string,unknown>[]}).__sent].reverse().find(m=>m.kind==="wizardReview")!);
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:review.clientRequestId,valid:true,preview:{coverage:{template_items:1,covered_items:1,required_items:1},coverage_contract:{contract_id:"c"},understanding_ref:{understanding_id:"u",revision:1}}});
  await page.getByRole("button",{name:"确认并发布",exact:true}).click();
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardCommands"&&m.stage==="commit")).toBe(true);
  await page.evaluate(()=>{
    const sent=(window as unknown as {__sent:Record<string,unknown>[]}).__sent;
    const request=[...sent].reverse().find(m=>m.kind==="wizardCommands"&&m.stage==="commit")!;
    window.postMessage({kind:"wizardResult",clientRequestId:request.clientRequestId,commands:[
      {key:"case-case-1",action:"save_case",intent_id:"i7",expected_revision:0,binding_revision:1,parameters:{}},
      {key:"scope",action:"save_acceptance",intent_id:"i8",expected_revision:0,binding_revision:1,parameters:{}},
      {key:"publish",action:"publish_plan",intent_id:"i9",expected_revision:0,binding_revision:1,parameters:{}},
    ]},"*");
  });
  for(const action of ["save_case","save_acceptance","publish_plan"]){
    await expect.poll(async()=> (await all()).at(-1)?.action).toBe(action);
    await postHostMessage(page,{kind:"outcome",action,status:"ok",response:{result:{plan_confirmation_id:"pc",grant_id:"g",basis_confirmations:[]}}});
  }
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardCommands"&&m.stage==="prepare")).toBe(true);
  await page.evaluate(()=>{
    const sent=(window as unknown as {__sent:Record<string,unknown>[]}).__sent;
    const request=[...sent].reverse().find(m=>m.kind==="wizardCommands"&&m.stage==="prepare")!;
    window.postMessage({kind:"wizardResult",clientRequestId:request.clientRequestId,commands:[{key:"prepare",action:"prepare_run",intent_id:"i10",expected_revision:0,binding_revision:1,parameters:{}}]},"*");
  });
  await expect.poll(async()=> (await all()).at(-1)?.action).toBe("prepare_run");
  await postHostMessage(page,{kind:"outcome",action:"prepare_run",status:"ok",response:{result:{prepared_run_id:"pr",status:"prepared"}}});
  await expect.poll(async()=> (await all()).some(m=>m.kind==="wizardCommands"&&m.stage==="register")).toBe(true);
  await page.evaluate(()=>{
    const sent=(window as unknown as {__sent:Record<string,unknown>[]}).__sent;
    const request=[...sent].reverse().find(m=>m.kind==="wizardCommands"&&m.stage==="register")!;
    window.postMessage({kind:"wizardResult",clientRequestId:request.clientRequestId,commands:[{key:"register",action:"register_run",intent_id:"i11",expected_revision:0,binding_revision:1,parameters:{}}]},"*");
  });
  await expect.poll(async()=> (await all()).at(-1)?.action).toBe("register_run");
  await postHostMessage(page,{kind:"outcome",action:"register_run",status:"ok",response:{result:{run_id:"run-1"}}});
  await expect(page.getByText("已准备运行",{exact:true})).toBeVisible();
});

test("host wizard errors are surfaced instead of leaving the step silent",async({page})=>{
  await mount(page);
  await page.getByRole("button",{name:"选择项目目录",exact:true}).click();
  await expect.poll(async()=> (await sent(page)).kind).toBe("wizardChoose");
  const request=await sent(page);
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:request.clientRequestId,error:"当前项目已变化，请回到原项目再继续。"});
  await expect(page.getByText("当前项目已变化",{exact:false})).toBeVisible();
});
