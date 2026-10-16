import {test,expect} from "@playwright/test";
import {resolve} from "node:path";
import {postHostMessage} from "./host-outcome";

test("workflow keeps execution identity, separates other intents and never auto submits",async({page})=>{
  await page.addInitScript(()=>{
    (window as unknown as {__sent:unknown[]}).__sent=[];
    (window as unknown as {acquireVsCodeApi:unknown}).acquireVsCodeApi=()=>({
      postMessage:(message:unknown)=>(window as unknown as {__sent:unknown[]}).__sent.push(message),
    });
  });
  await page.goto("about:blank"); await page.setContent("<ai-test-panel></ai-test-panel>");
  await page.addScriptTag({path:resolve("dist/index.js")});
  await page.getByRole("button",{name:"更多",exact:true}).click();
  await postHostMessage(page,{kind:"projectContext",project_id:"project",context_id:"scope"});
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{
    workspace_id:"workspace",result:{status:"READY",transport_capabilities:["native-confirmation/1"]},
  }});
  const action=page.getByRole("combobox",{name:"流程动作"});
  const intent=page.getByRole("textbox",{name:"本动作稳定业务意图"});
  const draft=page.getByRole("textbox",{name:"受控操作 JSON"});
  await action.selectOption("prepare_execution");
  await intent.fill("execute-once");
  await page.getByRole("textbox",{name:"运行编号",exact:true}).fill("run");
  await page.getByRole("textbox",{name:"步骤编号",exact:true}).fill("step");
  await page.getByRole("button",{name:"准备本步骤操作"}).click();
  expect(JSON.parse(await draft.inputValue())).toMatchObject({action:"prepare_execution",intent_id:"execute-once",
    target:"step",parameters:{run_id:"run",step_id:"step"}});
  expect(await page.evaluate(()=>(window as unknown as {__sent:unknown[]}).__sent.length)).toBe(1);
  await action.selectOption("authorize_step");
  await expect(intent).toHaveValue("execute-once");
  await page.getByRole("textbox",{name:"已解析动作编号"}).fill("saved-action");
  await page.getByRole("button",{name:"准备本步骤操作"}).click();
  expect(JSON.parse(await draft.inputValue()).parameters).toEqual({execution_action_id:"saved-action",record_revision:1});
  await action.selectOption("execute_step"); await expect(intent).toHaveValue("execute-once");
  await action.selectOption("verify_pending"); await expect(intent).toHaveValue("");
  await intent.fill("query-once");
  await page.getByRole("textbox",{name:"当前尝试编号"}).fill("attempt");
  await page.getByRole("textbox",{name:"当前执行快照编号"}).fill("snapshot");
  await page.getByRole("button",{name:"准备本步骤操作"}).click();
  await page.getByRole("button",{name:"请求核心确认并提交"}).click();
  const sent=await page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1));
  expect(sent).toMatchObject({action:"verify_pending",intentId:"query-once",contextId:"scope",
    parameters:{run_id:"run",step_id:"step",attempt_id:"attempt",base_snapshot_commit_id:"snapshot"}});
  await postHostMessage(page,{kind:"outcome",action:"verify_pending",status:"unavailable",code:"UNKNOWN",message:"unknown"});
  await expect(intent).toHaveValue("query-once");
  await postHostMessage(page,{kind:"projectContext",project_id:"other",context_id:"other-scope"});
  await expect(intent).toHaveValue(""); await expect(draft).toHaveValue("");
});

test("saved preparation form creates explicit references without frozen case fabrication",async()=>{
  const {workflowCommand}=await import("../src/workflow");
  const values:Record<string,string>={intent_id:"prepare",expected_revision:"0",project_revision:"1",
    binding_id:"binding",binding_revision:"2",plan_id:"plan",plan_revision:"3",snapshot_id:"source",
    snapshot_revision:"1",environment_id:"environment",environment_revision:"1",client_id:"client",
    prepare_request_id:"stable-request",execution_source:JSON.stringify({registered_entry:"python-main",
      entry_arguments:["main.py"],cwd_mapping:"workdir:.",test_config_ref:"configured",
      adapter_versions:{command:"1.0.0"}}),selected_case_ids:'["case"]',confirmation_refs:'[]'};
  const command=workflowCommand("prepare_run",values);
  expect(command.parameters).toMatchObject({mode:"from_saved",plan_revision:3,
    execution_source:{resolved_input_digest:"@core-assigned"},selected_case_ids:["case"]});
  expect(command.parameters).not.toHaveProperty("frozen_cases");
  expect(()=>workflowCommand("prepare_run",{...values,plan_revision:"1.5"})).toThrow();
});

test("run controls bind the exact run snapshot and delivery keeps report references explicit",async()=>{
  const {workflowCommand}=await import("../src/workflow");
  for (const action of ["start_run","pause_run","resume_run","cancel_run"]) {
    expect(workflowCommand(action,{intent_id:"control",expected_revision:"0",run_id:"run",
      base_snapshot_commit_id:"snapshot"})).toEqual({action,intent_id:"control",expected_revision:0,
      target:"run",parameters:{run_id:"run",base_snapshot_commit_id:"snapshot"}});
    expect(()=>workflowCommand(action,{intent_id:"control",expected_revision:"0",run_id:"run"})).toThrow();
  }
  const values={intent_id:"submission",expected_revision:"0",project_revision:"1",submission_id:"s",
    delivery_ref:'{"delivery_id":"d","record_revision":1}',
    source_ref:'{"snapshot_id":"source","record_revision":2}'};
  expect(workflowCommand("submit_delivery",values).parameters).not.toHaveProperty("verification_report_ref");
  expect(workflowCommand("submit_delivery",{...values,
    verification_report_ref:'{"report_id":"report","record_revision":3}'}).parameters)
    .toMatchObject({verification_report_ref:{report_id:"report",record_revision:3},expected_revision:0});
});
