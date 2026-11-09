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
  await expect(page.getByText("case-1 · 已完成",{exact:true})).toBeVisible();
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
