/** Entry forms only. Every result and permission remains owned by the core. */
export type Field = {key:string; label:string; kind?:"json"|"number"; optional?:boolean};
export const workflowActions: ReadonlyArray<{action:string; title:string; fields:Field[]}> = [
  {action:"confirm_basis", title:"确认准确断言依据（不代替执行/核验）", fields:[
    {key:"case_id",label:"用例编号"},{key:"case_revision",label:"用例仓库修订",kind:"number"},
    {key:"basis_revision",label:"依据修订",kind:"number"},{key:"basis_text_digest",label:"依据原文摘要"}]},
  {action:"publish_plan", title:"1. 发布计划", fields:[
    {key:"plan_id",label:"计划编号"},{key:"revision",label:"计划修订",kind:"number"},
    {key:"scope",label:"已保存范围 JSON",kind:"json"},
    {key:"cases",label:"已保存用例列表 JSON",kind:"json"},
    {key:"template_versions",label:"模板版本列表 JSON（报告需冻结模板版本）",kind:"json"},
    {key:"run_tier",label:"运行档位（full / quick / on_demand）"},
    {key:"initial_driver",label:"驱动（planned / stepwise）"}]},
  {action:"prepare_run", title:"2. 准备运行", fields:[
    {key:"project_revision",label:"项目仓库修订",kind:"number"},
    {key:"binding_id",label:"源码绑定编号"},{key:"binding_revision",label:"绑定仓库修订",kind:"number"},
    {key:"plan_id",label:"计划编号"},{key:"plan_revision",label:"计划仓库修订",kind:"number"},
    {key:"snapshot_id",label:"prepare 源码快照编号"},{key:"snapshot_revision",label:"源码快照仓库修订",kind:"number"},
    {key:"environment_id",label:"环境编号"},{key:"environment_revision",label:"环境仓库修订",kind:"number"},
    {key:"client_id",label:"持久客户端编号"},{key:"prepare_request_id",label:"本次准备编号（重传不变）"},
    {key:"execution_source",label:"明确执行入口 JSON",kind:"json"},
    {key:"selected_case_ids",label:"所选用例编号列表 JSON",kind:"json"},
    {key:"confirmation_refs",label:"准确确认回执引用列表 JSON（未确认填 []）",kind:"json"}]},
  {action:"register_run", title:"3. 登记运行", fields:[{key:"prepared_run_id",label:"准备记录编号"}]},
  {action:"prepare_execution", title:"4. 解析步骤动作", fields:[
    {key:"run_id",label:"运行编号"},{key:"step_id",label:"步骤编号"}]},
  {action:"authorize_step", title:"5. 授权步骤", fields:[
    {key:"step_id",label:"步骤编号"},{key:"execution_action_id",label:"已解析动作编号"}]},
  {action:"execute_step", title:"6. 执行步骤", fields:[
    {key:"step_id",label:"步骤编号"},{key:"execution_action_id",label:"已解析动作编号"}]},
  {action:"verify_pending", title:"7. 独立核验", fields:[
    {key:"run_id",label:"运行编号"},{key:"step_id",label:"步骤编号"},
    {key:"attempt_id",label:"当前尝试编号"},{key:"base_snapshot_commit_id",label:"当前执行快照编号"}]},
  {action:"create_report", title:"8. 保存报告", fields:[
    {key:"run_id",label:"运行编号"},{key:"report_id",label:"报告编号"},
    {key:"issue_ids",label:"关联问题编号列表 JSON（无问题填 []）",kind:"json"}]},
  {action:"export_report", title:"9. 导出报告", fields:[
    {key:"report_id",label:"报告编号"},{key:"kind",label:"制品类型（markdown_summary / evidence_bundle）"},
    {key:"review_ids",label:"复核编号列表 JSON（不附复核填 []）",kind:"json"}]},
  ...[
    ["start_run","按计划推进已授权步骤"], ["pause_run","暂停运行"],
    ["resume_run","恢复运行"], ["cancel_run","请求取消运行"],
  ].map(([action,title])=>({action:action!,title:title!,fields:[
    {key:"run_id",label:"运行编号"},
    {key:"base_snapshot_commit_id",label:"当前执行快照编号"},
  ]})),
  {action:"submit_delivery", title:"正式提测并关联报告", fields:[
    {key:"project_revision",label:"项目仓库修订",kind:"number"},
    {key:"submission_id",label:"本次提测编号"},
    {key:"delivery_ref",label:"交付准确引用 JSON",kind:"json"},
    {key:"source_ref",label:"源码准确引用 JSON",kind:"json"},
    {key:"verification_report_ref",label:"核验报告准确引用 JSON（可留空）",kind:"json",optional:true},
  ]},
];

export function workflowCommand(action:string, values:Record<string,string>):Record<string,unknown> {
  const spec = workflowActions.find(item=>item.action===action);
  if (!spec) { throw new Error("请选择流程动作"); }
  const intent = values["intent_id"]?.trim();
  const revision = Number(values["expected_revision"]);
  if (!intent || !values["expected_revision"]?.trim() || !Number.isSafeInteger(revision) || revision < 0) {
    throw new Error("请填写稳定意图和非负整数预期修订");
  }
  let parameters:Record<string,unknown> = {};
  for (const field of spec.fields) {
    const raw = values[field.key]?.trim();
    if (!raw && field.optional) { continue; }
    if (!raw) { throw new Error(`请填写${field.label}`); }
    let value:unknown = raw;
    if (field.kind === "json") { value = JSON.parse(raw); }
    if (field.kind === "number") {
      value = Number(raw);
      if (!Number.isSafeInteger(value) || Number(value) < 1) { throw new Error(`${field.label}须为正整数`); }
    }
    parameters[field.key] = value;
  }
  let target:unknown;
  if (action === "confirm_basis") {target=parameters["case_id"];}
  if (["start_run","pause_run","resume_run","cancel_run"].includes(action)) {
    target=parameters["run_id"];
  }
  if (action === "submit_delivery") {
    target=parameters["submission_id"]; parameters["expected_revision"]=revision;
  }
  if (action === "prepare_run") {
    const source=parameters["execution_source"];
    if (!source || typeof source!=="object" || Array.isArray(source)) {
      throw new Error("执行入口须为对象");
    }
    parameters["execution_source"]={...source,resolved_input_digest:"@core-assigned"};
    parameters["mode"]="from_saved";
  }
  if (action === "register_run") { target=parameters["prepared_run_id"]; parameters["record_revision"]=1; }
  if (["prepare_execution","authorize_step","execute_step","verify_pending"].includes(action)) {
    target=parameters["step_id"];
  }
  if (["authorize_step","execute_step"].includes(action)) {
    delete parameters["step_id"]; parameters["record_revision"]=1;
  }
  for (const key of ["issue_ids","review_ids"]) {
    if (key in parameters && (!Array.isArray(parameters[key])
        || (parameters[key] as unknown[]).some(value=>typeof value!=="string" || !value.trim()))) {
      throw new Error("关联记录须为编号数组");
    }
  }
  return {action,intent_id:intent,expected_revision:revision,...(target ? {target}:{}),parameters};
}
