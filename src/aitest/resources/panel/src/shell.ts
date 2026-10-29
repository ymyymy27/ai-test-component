/** Editor-scale shell. All business values are supplied by the core. */
export const shellStyles = `
:host{display:block;min-height:100vh;background:var(--vscode-sideBar-background,#22262c);color:var(--vscode-foreground,#d5d9df);font:var(--vscode-font-size,13px)/1.5 var(--vscode-font-family,system-ui)}
*{box-sizing:border-box}main{padding:0 12px 16px;min-width:0}header{padding:10px 0;border-bottom:1px solid var(--vscode-panel-border,#555)}
h1,h2,h3,p{margin:0 0 6px}h1{font-size:14px}h2,h3{font-size:13px}section{padding:10px 0;min-width:0}
.toolbar,.actions,.details{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.toolbar h1{flex:1;margin:0}
button,input,textarea,select{font:inherit;color:inherit;max-width:100%}button{border:1px solid transparent;padding:4px 8px;border-radius:2px;background:var(--vscode-button-secondaryBackground,#383838);cursor:pointer}
button.primary{background:var(--vscode-button-background,#007acc);color:var(--vscode-button-foreground,#fff)}button:disabled{opacity:.55;cursor:not-allowed}
input,textarea,select{display:block;width:100%;padding:5px 6px;background:var(--vscode-input-background,#303030);border:1px solid var(--vscode-input-border,#666);border-radius:2px}
textarea{resize:vertical;min-height:60px}label{display:block;margin:8px 0}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:1px solid var(--vscode-focusBorder,#007acc);outline-offset:2px}
input[type=checkbox]{display:inline-block;width:auto;margin:0 6px 0 0;vertical-align:middle}fieldset{min-width:0;margin:10px 0;border:1px solid var(--vscode-panel-border,#555)}
details.group{border-bottom:1px solid var(--vscode-panel-border,#555)}summary{cursor:pointer;padding:9px 0;font-weight:600}details.group>div{padding:0 0 10px}
.muted{color:var(--vscode-descriptionForeground,#aaa)}.scope{overflow-wrap:anywhere}.status{padding:6px 0;overflow-wrap:anywhere}.row{padding:6px 0;border-bottom:1px solid var(--vscode-panel-border,#555)}
ul{list-style:none;padding:0;margin:0}li{padding:4px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}dd{margin:0 0 8px;overflow-wrap:anywhere}dt{color:var(--vscode-descriptionForeground,#aaa)}
.phase{padding:4px 0}.next{padding:6px 0}.actions{margin:8px 0}.record-id{overflow-wrap:anywhere}footer{padding-top:10px;font-size:12px}.diagnostics{margin-top:12px}
.workbench-part,.check-form{border-top:1px solid var(--vscode-panel-border,#363b44);padding:12px 0}.workbench-part h3{margin-bottom:6px;font-weight:500}.workbench-part p{font-size:12px}.check-form{padding:0}.check-form>summary{font-weight:500}.check-form .form-part{padding:0 4px 12px}
main{min-height:100vh;display:flex;flex-direction:column}.dashboard{display:flex;flex-direction:column;flex:1}.dashboard>footer,main>footer{margin-top:auto}section[data-testid=detail-view]{flex:1}
.field-error{display:block;margin-top:5px;color:var(--vscode-errorForeground,#f48771);font-size:12px}input[aria-invalid=true]{border-color:var(--vscode-inputValidation-errorBorder,#be5555)}.link-row:empty{display:none}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
main{padding:0 14px 16px;max-width:820px;margin:0 auto}header{padding:0;border:0}.toolbar{min-height:40px}.toolbar button,.link-button{background:transparent;color:var(--vscode-textLink-foreground,#85baff);padding:3px 0}.toolbar button{padding:4px 6px}.toolbar h1{font-size:13px;font-weight:600}
.project-picker{margin:0 -14px;border-bottom:1px solid var(--vscode-panel-border,#363b44);padding:7px 14px}.project-picker>summary{padding:0;font-weight:400;overflow-wrap:anywhere}.project-picker .actions{margin-bottom:0}.model-line{display:flex;align-items:center;justify-content:space-between;gap:8px;padding:8px 0;margin:0;font-size:12px}.model-line button{background:transparent;color:var(--vscode-textLink-foreground,#85baff);padding:0}.model-line span{overflow-wrap:anywhere}
details.group{margin:0 -14px;border-color:var(--vscode-panel-border,#363b44)}details.group>summary{padding:9px 14px;font-size:12px;font-weight:500}details.group>div{padding:0 14px 12px}details.group section{padding:2px 0}.group .actions{margin:8px 0}.phase h2{font-size:15px;font-weight:500;margin-bottom:4px}.phase p{margin-bottom:4px}.phase{padding:2px 0 4px}.subtle{font-size:12px}.link-row{display:flex;gap:14px;flex-wrap:wrap;margin:8px 0}.link-row button{background:transparent;color:var(--vscode-textLink-foreground,#85baff);padding:0;text-align:left}
.facts{display:grid;grid-template-columns:minmax(58px,auto) 1fr;gap:6px 12px;margin:10px 0}.facts dt,.facts dd{margin:0}.facts dd{min-width:0}.case-tree ul{margin:4px 0 4px 7px;border-left:1px solid var(--vscode-panel-border,#363b44);padding-left:10px}.case-tree button{background:transparent;text-align:left;width:100%;padding:3px 4px}.case-tree button:hover{background:var(--vscode-list-hoverBackground,#2c313a)}.case-tree strong{font-weight:500}.empty{padding:8px 0;color:var(--vscode-descriptionForeground,#a6aebc)}
footer{border-top:1px solid var(--vscode-panel-border,#363b44);margin:12px -14px -16px;padding:9px 14px;font-size:11px}.detail-heading{padding:10px 0;border-bottom:1px solid var(--vscode-panel-border,#363b44);margin-bottom:16px}.detail-heading button{background:transparent;color:var(--vscode-textLink-foreground,#85baff);padding:0;margin-bottom:8px}.detail-heading h1{font-size:15px;font-weight:500}.wizard-steps{display:flex;list-style:none;gap:0;margin:12px 0 18px;padding:0;border-bottom:1px solid var(--vscode-panel-border,#363b44);font-size:12px}.wizard-steps li{flex:1;padding:6px 2px;text-align:center;color:var(--vscode-descriptionForeground,#a6aebc);border-bottom:2px solid transparent}.wizard-steps [aria-current=step]{color:var(--vscode-foreground,#d5d9df);border-color:var(--vscode-focusBorder,#3686d7)}.form-part{padding:4px 0 14px}.form-part h3{font-size:14px;font-weight:500;margin:4px 0 8px}.form-part label>span{display:block;margin-bottom:5px}.form-part label{margin:12px 0}.form-part p{font-size:12px;color:var(--vscode-descriptionForeground,#a6aebc)}.wizard-nav{display:flex;gap:8px;align-items:center;margin:16px 0 8px}.wizard-nav .primary{margin-left:auto}.notice{border-left:2px solid var(--vscode-focusBorder,#3686d7);padding:6px 10px;margin:10px 0;overflow-wrap:anywhere}.technical{margin-top:12px;font-size:12px}.technical summary{font-weight:400}button:hover:not(:disabled){filter:brightness(1.12)}button:disabled{opacity:.5}button{min-height:27px}fieldset{padding:10px 12px}legend{font-weight:500}input,select{min-height:32px}section[data-testid=detail-view]{padding-top:0}
.form-part>h3{margin-top:20px;padding-top:12px;border-top:1px solid var(--vscode-panel-border,#363b44)}.form-part>h3:first-child{margin-top:0;padding-top:0;border:0}.form-part input,.form-part select,.form-part textarea{background:var(--vscode-input-background,#191d23);border-color:var(--vscode-input-border,#39404a)}.form-part [role=status]:empty{display:none}.form-part>button.primary{margin-top:8px}
@media(max-width:320px){.wizard-steps{font-size:11px}.facts{grid-template-columns:58px 1fr;gap:5px 8px}.toolbar{gap:2px}}
.report-outcome{display:block;font-size:15px;font-weight:600;margin:2px 0 6px}
.pending-list{display:flex;flex-direction:column;align-items:flex-start;gap:3px;margin:4px 0 6px}
.pending-list button{text-align:left}
.badge{display:inline-block;padding:1px 8px;border-radius:10px;border:1px solid var(--vscode-panel-border,#4a505a);font-size:12px;margin:2px 0 6px}
.badge[data-control=running]{color:#7fd1a0;border-color:#3d6b52}
.badge[data-control=paused],.badge[data-control=pause_requested]{color:#e0c07a;border-color:#6b5f33}
.badge[data-control=cancelled],.badge[data-control=cancelling]{color:#e08a7a;border-color:#6b3d35}
.badge[data-outcome=passed]{color:#7fd1a0;border-color:#3d6b52}
.badge[data-outcome=failed]{color:#e08a7a;border-color:#6b3d35}
.badge[data-outcome=incomplete]{color:#e0c07a;border-color:#6b5f33}
.progress{height:4px;background:var(--vscode-panel-border,#3a4048);border-radius:2px;margin:2px 0 8px;overflow:hidden}
.progress>div{height:100%;background:var(--vscode-progressBar-background,#0e70c0)}
.stat-row{display:flex;gap:10px;margin:10px 0 8px}
.stat{flex:1;min-width:0}
.stat b{display:block;font-size:18px;font-weight:600;line-height:1.2}
.stat span{display:block;font-size:11px;color:var(--vscode-descriptionForeground,#a6aebc)}
.hint{padding-left:10px;font-size:12px;color:var(--vscode-descriptionForeground,#a6aebc);margin:0 0 6px}
/* 窄侧栏用文字行，宽页面（编辑器标签页）用表格；同一份数据两种呈现。 */
.coverage-table{display:none}
.tag{font-size:11px;color:var(--vscode-descriptionForeground,#a6aebc);margin-left:6px}
/* 编辑器标签页：隐藏只属于侧栏的头部（面板用影子树，必须用 :host(...) 选择器）。 */
:host(.detail-only) header{display:none}
.gap-box{border-left:3px solid var(--vscode-editorWarning-foreground,#d7a23a);background:var(--vscode-input-background,#191d23);padding:8px 12px;margin:10px 0;border-radius:2px}
.pending-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
@media(min-width:620px){
  main{max-width:1080px}
  .coverage-lines{display:none}
  table.coverage-table{display:table;width:100%;border-collapse:collapse;margin:8px 0;font-size:12px}
  table.coverage-table th,table.coverage-table td{padding:6px 8px;border-bottom:1px solid var(--vscode-panel-border,#363b44);text-align:left}
  table.coverage-table tr:first-child th{color:var(--vscode-descriptionForeground,#a6aebc);font-weight:500}
  table.coverage-table td{text-align:right}
  table.coverage-table tr:first-child th:not(:first-child){text-align:right}
  /* 宽页面里事实表排成两列，和设计稿的"执行状态 | 断言与核验"一致。 */
  .facts{grid-template-columns:max-content 1fr max-content 1fr;gap:8px 16px}
}
`;

export function text(tag:string, value:string, className=""):HTMLElement {
  const element=document.createElement(tag);element.textContent=value;element.className=className;return element;
}
/** 缺口代码在领域里是英文；面板显示中文说明，原始代码仍留在字段细节中。 */
const GAP_TEXT:Record<string,string>={
  source_identity_mismatch:"源码与登记身份不一致",
  no_valid_execution_basis:"没有可用的执行依据",
  source_identity_unverified:"源码身份尚未核实",
  template_required_below_floor:"必测项低于模板要求",
  full_required_scope_missing:"完整验证缺少必测范围",
  required_case_unresolved:"必测用例尚未得出结论",
  required_case_pending_verification:"必测用例待核验",
  required_case_unverified:"必测用例本轮没有取得有效验证",
  outdated_basis:"断言依据已过期",
  required_evidence_invalid:"必需证据缺失、损坏或无效",
  environment_evidence_incomplete:"环境证据不完整",
  critical_path_missing:"缺少关键链路覆盖",
  critical_unknown:"关键链路结果未知",
  critical_mock:"关键链路使用了模拟",
  full_skipped_scope:"完整验证跳过了部分范围",
  assertion_basis_unconfirmed:"断言依据尚未确认",
  noncritical_unknown:"非关键项结果未知",
  noncritical_verification_gap:"非关键项缺少验证",
  unclassified_gap:"未分类的缺口",
};
export function gapText(code:unknown, fallback:unknown):string {
  if(typeof code==="string" && GAP_TEXT[code])return GAP_TEXT[code];
  return typeof fallback==="string" && fallback.trim() ? fallback : "缺口";
}
/** 每个缺口下一步该做什么；只描述动作，不替用户判断结论。 */
const GAP_HINT:Record<string,string>={
  source_identity_mismatch:"重新分析源码，确认被测内容与登记一致后再执行。",
  no_valid_execution_basis:"先确认计划与断言依据，再开始执行。",
  source_identity_unverified:"重新分析源码，让核心登记当前来源。",
  template_required_below_floor:"按模板补齐必测项后再发布计划。",
  full_required_scope_missing:"检查必测范围是否被排除，恢复后再执行。",
  required_case_unresolved:"先执行该用例并记录实际结果。",
  required_case_pending_verification:"打开该步骤，执行“核验这次执行结果”。",
  required_case_unverified:"核验通过后再保存一次报告。",
  outdated_basis:"上游结果已变化，重新执行受影响的用例。",
  required_evidence_invalid:"重新执行以产生新的证据，再核验。",
  environment_evidence_incomplete:"在环境页补充说明，然后重新准备本次运行。",
  critical_path_missing:"补上关键链路的执行记录。",
  critical_unknown:"关键链路结果未知，需要重新执行确认。",
  critical_mock:"关键链路使用了模拟，改用真实执行。",
  full_skipped_scope:"把被跳过的范围重新纳入本轮。",
  assertion_basis_unconfirmed:"先确认断言依据，再进行核验。",
  noncritical_unknown:"重新执行该用例以取得明确结果。",
  noncritical_verification_gap:"补充该用例的核验。",
  unclassified_gap:"按缺口说明补齐对应材料。",
};
export function gapHint(code:unknown):string {
  return typeof code==="string" ? (GAP_HINT[code] ?? "") : "";
}
export function action(label:string, click:()=>void, disabled=false):HTMLButtonElement {
  const button=document.createElement("button");button.textContent=label;button.onclick=click;button.disabled=disabled;return button;
}
export function group(label:string, key:string, open:boolean, onToggle:(open:boolean)=>void, ...children:HTMLElement[]):HTMLDetailsElement {
  const details=document.createElement("details");details.className="group";details.dataset.group=key;details.open=open;
  const summary=document.createElement("summary");summary.textContent=label;
  const content=document.createElement("div");content.append(...children);details.append(summary,content);
  details.ontoggle=()=>onToggle(details.open);return details;
}
export function object(value:unknown):Record<string,unknown>|undefined {
  return value!==null && typeof value==="object" && !Array.isArray(value) ? value as Record<string,unknown> : undefined;
}
export function strings(value:unknown):string[] {return Array.isArray(value) ? value.filter((x):x is string=>typeof x==="string") : [];}

export function newIntent():string {return Array.from(crypto.getRandomValues(new Uint8Array(24)),value=>value.toString(16).padStart(2,"0")).join("");}
