/** Editor-scale shell. All business values are supplied by the core. */
export const shellStyles = `:host{
  --ink:var(--vscode-foreground,#e6e8ea);
  --muted:var(--vscode-descriptionForeground,#9aa3ad);
  --surface:var(--vscode-editorWidget-background,#1d2229);
  --surface-2:var(--vscode-sideBar-background,#181c22);
  --line:var(--vscode-widget-border,rgba(255,255,255,.09));
  --accent:var(--vscode-focusBorder,#4c9aff);
  --accent-ink:var(--vscode-button-foreground,#fff);
  --ok:#5fd18b;--warn:#e4bd67;--bad:#ef8c7f;
  display:block;min-height:100vh;background:var(--vscode-editor-background,#15191f);color:var(--ink);
  font:13px/1.55 var(--vscode-font-family,system-ui,-apple-system,"Segoe UI",sans-serif);
}
*{box-sizing:border-box}
button,input,textarea,select{font:inherit;color:inherit}
button{border:1px solid var(--line);background:var(--surface);border-radius:9px;padding:7px 11px;cursor:pointer;min-height:30px;transition:background .15s,border-color .15s,transform .05s}
button:hover:not(:disabled){background:var(--vscode-list-hoverBackground,#252c35);border-color:color-mix(in srgb,var(--accent) 35%,var(--line))}
button:active:not(:disabled){transform:translateY(1px)}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--accent-ink);font-weight:600}
button.primary:hover:not(:disabled){background:color-mix(in srgb,var(--accent) 86%,#fff)}
button.link-button{background:transparent;border-color:transparent;color:var(--accent);padding:3px 0;min-height:0}
input,textarea,select{display:block;width:100%;padding:8px 10px;background:var(--vscode-input-background,#11151b);border:1px solid var(--vscode-input-border,var(--line));border-radius:8px;min-height:34px}
textarea{resize:vertical;min-height:76px}
input:focus,textarea:focus,select:focus,button:focus-visible,summary:focus-visible{outline:2px solid color-mix(in srgb,var(--accent) 65%,transparent);outline-offset:1px}
input[type=checkbox]{display:inline-block;width:auto;min-height:0;margin:0 8px 0 0;vertical-align:middle;accent-color:var(--accent)}
label{display:block;margin:10px 0}
label>span{display:block;margin-bottom:5px;font-size:12px;color:var(--muted)}
fieldset{min-width:0;margin:10px 0;border:1px solid var(--line);border-radius:10px;padding:10px 12px}
legend{font-weight:600;padding:0 4px}
h1,h2,h3,h4,p{margin:0 0 8px}
h1{font-size:20px;line-height:1.25;font-weight:650;letter-spacing:-.01em}
h2{font-size:15px;font-weight:600}
h3{font-size:13px;font-weight:600}
h4{font-size:12px;font-weight:600;color:var(--muted);text-transform:uppercase;letter-spacing:.06em}
.muted{color:var(--muted)}.subtle{font-size:12px}
.app{display:grid;grid-template-columns:1fr;min-height:100vh}
.rail{display:flex;gap:4px;align-items:center;overflow-x:auto;padding:8px 10px;background:var(--surface-2);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5;scrollbar-width:none}
.rail::-webkit-scrollbar{display:none}
.brand{display:none}
.rail-item{display:inline-flex;align-items:center;gap:6px;border:0;background:transparent;color:var(--muted);padding:7px 10px;border-radius:8px;white-space:nowrap;font-size:12px}
.rail-item .rail-icon{font-size:14px;opacity:.9}
.rail-item:hover{background:var(--vscode-list-hoverBackground,#252c35);color:var(--ink)}
.rail-item.active{background:color-mix(in srgb,var(--accent) 16%,transparent);color:var(--ink);font-weight:600;box-shadow:inset 0 0 0 1px color-mix(in srgb,var(--accent) 30%,transparent)}
.workspace{min-width:0;padding:14px 14px 28px;max-width:900px;margin:0 auto;width:100%}
.page{display:grid;gap:12px}
.card{background:var(--surface);border:1px solid var(--line);border-radius:14px;padding:15px 16px;min-width:0;box-shadow:0 1px 0 rgba(255,255,255,.02)}
.card-title{margin:0 0 10px;font-size:12px;letter-spacing:.08em;text-transform:uppercase;color:var(--muted)}
.hero{background:linear-gradient(135deg,color-mix(in srgb,var(--accent) 12%,var(--surface)),var(--surface) 55%);border-color:color-mix(in srgb,var(--accent) 22%,var(--line))}
.eyebrow{margin:0 0 8px;font-size:11px;letter-spacing:.14em;text-transform:uppercase;color:var(--accent);font-weight:700}
.topbar{display:grid;gap:8px;margin-bottom:14px}
.topbar-row{display:flex;gap:10px;align-items:flex-start;justify-content:space-between}
.topbar-actions{display:flex;gap:4px;flex-shrink:0}
.topbar-actions button{background:transparent;border-color:transparent;color:var(--muted);padding:6px 8px;min-height:0}
.topbar-actions button:hover{color:var(--ink)}
.project-card{flex:1;min-width:0;background:var(--surface);border:1px solid var(--line);border-radius:12px;padding:9px 12px}
.project-card summary{display:flex;align-items:center;gap:8px;padding:0;cursor:pointer;list-style:none;font-weight:600}
.project-card summary::-webkit-details-marker{display:none}
.project-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
.project-switch{margin-left:auto;font-size:11px;color:var(--accent);font-weight:500}
.status-dot{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none;box-shadow:0 0 0 3px color-mix(in srgb,var(--muted) 16%,transparent)}
.status-dot[data-state=ready]{background:var(--ok);box-shadow:0 0 0 3px color-mix(in srgb,var(--ok) 18%,transparent)}
.project-card>p{margin:7px 0 0;font-size:11px;overflow-wrap:anywhere}
.project-card .actions{margin:8px 0 0}
.topbar-meta{display:flex;align-items:center;gap:8px;flex-wrap:wrap;font-size:11px}
.topbar-meta button{background:transparent;border:0;color:var(--accent);padding:0;min-height:0}
.meta-pill{padding:2px 8px;border:1px solid var(--line);border-radius:999px;color:var(--muted)}
.actions,.details,.link-row{display:flex;align-items:center;flex-wrap:wrap;gap:7px}
.link-row{margin:8px 0 0}
.link-row button{background:transparent;border:0;color:var(--accent);padding:0;min-height:0;text-align:left}
section{padding:0;min-width:0}
details.group{border:0;border-top:1px solid var(--line);margin:8px 0 0}
details.group>summary{padding:8px 0;font-weight:600;font-size:12px;color:var(--muted)}
details.group>div{padding:0 0 8px}
details.technical{margin-top:10px;font-size:12px}
details.technical summary{font-weight:500;color:var(--muted);cursor:pointer}
.phase{display:grid;gap:6px}
.phase h2{font-size:18px;margin:0}
.phase p{margin:0}
.phase .actions{margin:4px 0}
.phase button,.phase .primary{justify-self:start;width:auto}
.badge{display:inline-flex;align-items:center;gap:4px;width:fit-content;padding:2px 9px;border-radius:999px;border:1px solid var(--line);font-size:11px;margin:0;color:var(--muted);font-weight:600}
.badge[data-control=running]{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line));background:color-mix(in srgb,var(--ok) 10%,transparent)}
.badge[data-control=paused],.badge[data-control=pause_requested]{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn) 10%,transparent)}
.badge[data-control=cancelled],.badge[data-control=cancelling]{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 10%,transparent)}
.badge[data-outcome=passed]{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line));background:color-mix(in srgb,var(--ok) 10%,transparent)}
.badge[data-outcome=failed]{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,var(--line));background:color-mix(in srgb,var(--bad) 10%,transparent)}
.badge[data-outcome=incomplete]{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line));background:color-mix(in srgb,var(--warn) 10%,transparent)}
.progress{height:5px;background:color-mix(in srgb,var(--muted) 18%,transparent);border-radius:999px;margin:4px 0 8px;overflow:hidden}
.progress>div{height:100%;background:var(--accent);border-radius:inherit}
.stat-row{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:10px 0 6px}
.stat{background:color-mix(in srgb,var(--muted) 8%,transparent);border:1px solid var(--line);border-radius:10px;padding:9px}
.stat b{display:block;font-size:19px;font-weight:650;line-height:1.2}
.stat span{display:block;font-size:11px;color:var(--muted);margin-top:2px}
.pending-list{display:grid;gap:5px;margin:2px 0}
.pending-list button{text-align:left;justify-self:start}
.notice{border-left:3px solid var(--accent);background:color-mix(in srgb,var(--accent) 8%,transparent);padding:8px 10px;margin:8px 0;border-radius:0 8px 8px 0;overflow-wrap:anywhere}
.gap-box{border-left:3px solid var(--warn);background:color-mix(in srgb,var(--warn) 8%,transparent);padding:9px 11px;margin:8px 0;border-radius:0 8px 8px 0}
.row{padding:8px 0;border-bottom:1px solid var(--line)}
.row:last-child{border-bottom:0}
ul{list-style:none;padding:0;margin:0}li{padding:3px 0}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:260px;overflow:auto;background:var(--vscode-textCodeBlock-background,#11151b);border:1px solid var(--line);border-radius:8px;padding:10px}
dl{margin:0}dt{color:var(--muted);font-size:11px}dd{margin:0 0 8px;overflow-wrap:anywhere}
.facts{display:grid;grid-template-columns:minmax(64px,auto) 1fr;gap:6px 12px;margin:8px 0}
.facts dt,.facts dd{margin:0}.facts dd{min-width:0}
.case-tree ul{margin:4px 0 4px 8px;border-left:1px solid var(--line);padding-left:10px}
.case-tree button{background:transparent;border:0;text-align:left;width:100%;padding:5px 6px;border-radius:7px;min-height:0;color:var(--ink)}
.case-tree button:hover{background:var(--vscode-list-hoverBackground,#252c35)}
.case-tree strong{font-weight:600}.empty{padding:8px 0;color:var(--muted)}
.evidence-body{white-space:pre-wrap;overflow-wrap:anywhere;max-height:420px;overflow:auto;background:var(--vscode-textCodeBlock-background,#11151b);border:1px solid var(--line);border-radius:9px;padding:11px;margin:8px 0;user-select:text}
.field-error{display:block;margin-top:5px;color:var(--bad);font-size:12px}
input[aria-invalid=true]{border-color:var(--bad)}
.detail-heading{display:grid;gap:6px;padding:2px 0 12px;border-bottom:1px solid var(--line);margin-bottom:14px}
.detail-heading button{justify-self:start;background:transparent;border:0;color:var(--accent);padding:0;min-height:0}
.detail-heading h1{font-size:20px;margin:0}
.workbench-part{border-top:1px solid var(--line);padding:12px 0}
.workbench-part h3{margin-bottom:5px}
.workbench-part p{font-size:12px;color:var(--muted)}
.check-form{border:1px solid var(--line);border-radius:10px;margin:8px 0;padding:0;background:color-mix(in srgb,var(--muted) 4%,transparent)}
.check-form>summary{font-weight:600;padding:9px 11px}
.check-form .form-part{padding:0 11px 12px}
.form-part{padding:4px 0 12px}
.form-part h3{font-size:14px;margin:4px 0 8px}
.form-part p{font-size:12px;color:var(--muted)}
.form-part label{margin:11px 0}
.wizard-steps{display:flex;list-style:none;gap:6px;margin:12px 0 18px;padding:0;font-size:11px}
.wizard-steps li{flex:1;padding:6px 4px;text-align:center;color:var(--muted);border:1px solid var(--line);border-radius:999px;background:var(--surface)}
.wizard-steps [aria-current=step]{color:var(--ink);border-color:color-mix(in srgb,var(--accent) 55%,var(--line));background:color-mix(in srgb,var(--accent) 13%,transparent);font-weight:600}
.wizard-nav{display:flex;gap:8px;align-items:center;margin:16px 0 6px}
.wizard-nav .primary{margin-left:auto}
.step-strip{display:flex;flex-wrap:wrap;gap:6px;margin:6px 0 10px;padding-bottom:8px;border-bottom:1px solid var(--line)}
.step-tab{background:var(--surface);border:1px solid var(--line);border-radius:999px;padding:4px 10px;font-size:12px}
.step-tab.active{border-color:var(--accent);color:var(--accent);font-weight:600}
.tag{font-size:11px;color:var(--muted);margin-left:6px}
.coverage-lines{display:grid;gap:4px}
.coverage-table{display:none}
.report-outcome{display:block;font-size:18px;font-weight:650;margin:2px 0 6px}
.pending-row{display:flex;align-items:baseline;justify-content:space-between;gap:8px}
.scope,.record-id,.status{overflow-wrap:anywhere}
footer{border-top:1px solid var(--line);margin-top:6px;padding:10px 2px;font-size:11px;color:var(--muted)}
footer.is-error{color:var(--bad)}
@media(prefers-reduced-motion:reduce){*{transition:none!important;scroll-behavior:auto!important}}
@media(min-width:620px){
  .workspace{max-width:1040px;padding:20px 22px 34px}
  .card{padding:18px 20px}
  .home{grid-template-columns:1.15fr .85fr}
  .home>.card:nth-child(1),.home>.card:nth-child(2),.home>footer{grid-column:1/-1}
  .coverage-lines{display:none}
  table.coverage-table{display:table;width:100%;border-collapse:collapse;margin:8px 0;font-size:12px}
  table.coverage-table th,table.coverage-table td{padding:7px 8px;border-bottom:1px solid var(--line);text-align:left}
  table.coverage-table tr:first-child th{color:var(--muted);font-weight:600}
  table.coverage-table td{text-align:right}
}
:host(.standalone) .app{grid-template-columns:190px minmax(0,1fr)}
:host(.standalone) .rail{position:sticky;top:0;height:100vh;flex-direction:column;align-items:stretch;border-bottom:0;border-right:1px solid var(--line);padding:16px 10px;overflow:visible}
:host(.standalone) .brand{display:grid;gap:2px;padding:4px 8px 14px}
:host(.standalone) .brand-mark{font-size:14px;font-weight:700;letter-spacing:-.01em}
:host(.standalone) .brand-sub{font-size:11px;color:var(--muted);overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
:host(.standalone) .rail-item{width:100%;text-align:left;padding:9px 10px;font-size:12px}
:host(.standalone) .workspace{max-width:1120px;margin:0;padding:26px 32px 40px}
:host(.standalone) .topbar{display:flex}
/* IDE-native compact layout: side panel first, editor tab second. No hero/marketing cards. */
:host{font-size:12px;line-height:1.45}
.app{display:block}
.rail{height:34px;padding:0 6px;gap:0;background:var(--surface-2);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:5}
.rail-item{border:0;border-radius:0;background:transparent;padding:8px 9px;min-height:34px;font-size:12px;color:var(--muted);border-bottom:2px solid transparent}
.rail-item .rail-icon{display:none}
.rail-item:hover{background:transparent;color:var(--ink);border-bottom-color:color-mix(in srgb,var(--muted) 35%,transparent)}
.rail-item.active{background:transparent;box-shadow:none;border-bottom-color:var(--accent);color:var(--ink)}
.brand{display:none!important}
.workspace{max-width:none;margin:0;padding:0 10px 14px;width:100%}
.page{display:block}
.home{display:block}
.card,.hero{background:transparent;border:0;border-bottom:1px solid var(--line);border-radius:0;box-shadow:none;padding:10px 0;margin:0}
.card:last-of-type{border-bottom:0}
.hero{background:transparent;border-color:var(--line)}
.eyebrow{margin:0 0 4px;font-size:10px;letter-spacing:.12em;color:var(--muted);font-weight:600}
.phase{gap:4px}
.phase h2{font-size:14px;font-weight:600;margin:0}
.phase p{font-size:12px}
.card-title{font-size:10px;margin:0 0 6px;letter-spacing:.1em;color:var(--muted)}
.actions,.details,.link-row{gap:5px}
.actions{margin:6px 0}
button{min-height:24px;padding:4px 8px;border-radius:4px;font-size:12px}
button.primary{font-weight:600}
link-row button,.link-row button{min-height:0;padding:0}
input,textarea,select{min-height:28px;padding:4px 7px;border-radius:4px}
label{margin:8px 0}
label>span{font-size:11px;margin-bottom:3px}
h1{font-size:16px}h2{font-size:13px}h3{font-size:12px}h4{font-size:10px}
.topbar{margin:0 -10px;padding:7px 10px 6px;border-bottom:1px solid var(--line);background:var(--surface-2);gap:2px;position:sticky;top:34px;z-index:4}
.topbar-row{align-items:center;gap:6px}
.project-card{border:0;background:transparent;padding:0;border-radius:0;min-width:0}
.project-card summary{font-size:12px;gap:6px;font-weight:600}
.project-card summary::-webkit-details-marker{display:none}
.project-switch{display:none}
.project-card>p,.project-card .actions{display:none}
.project-card[open]{background:var(--surface);border:1px solid var(--line);border-radius:6px;padding:6px 8px;position:absolute;left:10px;right:10px;top:100%;z-index:8;box-shadow:0 6px 18px rgba(0,0,0,.35)}
.project-card[open]>p,.project-card[open] .actions{display:flex;font-size:11px;margin-top:6px}
.project-card[open]>p{display:block}
.topbar-actions{gap:2px}
.topbar-actions button{border:0;background:transparent;color:var(--muted);padding:4px 6px;min-height:24px}
.topbar-actions button:hover{color:var(--ink);background:var(--vscode-list-hoverBackground,#252c35)}
.topbar-meta{font-size:10px;gap:6px;padding:1px 0 0}
.meta-pill{padding:1px 6px}
.status-dot{width:7px;height:7px}
.stat-row{display:flex;gap:14px;margin:6px 0 2px;border:0}
.stat{background:transparent;border:0;padding:0;min-width:0}
.stat b{font-size:16px}
.stat span{font-size:10px}
.pending-list{gap:2px}
.pending-list button{padding:3px 0;min-height:0;border:0;background:transparent;color:var(--accent);text-align:left}
.notice{border-left:2px solid var(--accent);background:transparent;padding:5px 8px;margin:6px 0;border-radius:0}
.gap-box{border-left:2px solid var(--warn);background:transparent;padding:5px 8px;margin:6px 0;border-radius:0}
.badge{padding:1px 7px;font-size:10px}
.progress{height:3px;margin:4px 0 6px}
.case-tree ul{margin-left:6px;padding-left:8px}
.case-tree button{padding:4px 5px;font-size:12px}
.detail-heading{padding:0 0 8px;margin-bottom:8px;gap:2px}
.detail-heading h1{font-size:15px}
.detail-heading button{font-size:11px}
.workbench-part{padding:8px 0}
.check-form{border:0;border-top:1px solid var(--line);border-radius:0;margin:0;background:transparent}
.check-form>summary{padding:7px 0;color:var(--ink)}
.check-form .form-part{padding:0 0 8px}
.form-part{padding:2px 0 8px}
.form-part h3{font-size:13px;margin:2px 0 6px;border:0;padding:0}
.wizard-steps{gap:0;margin:8px 0 12px;border-bottom:1px solid var(--line)}
.wizard-steps li{flex:1;border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;padding:5px 2px;font-size:11px;text-align:left}
.wizard-steps [aria-current=step]{background:transparent;border-bottom-color:var(--accent)}
.wizard-nav{margin:10px 0 4px}
.form-part>h3{margin-top:10px;padding-top:6px}
.report-outcome{font-size:15px}
.coverage-lines{font-size:11px}
details.group{border-top:1px solid var(--line);margin:6px 0 0}
details.group>summary{padding:6px 0;font-size:11px}
footer{margin-top:0;padding:7px 0;font-size:10px;border-top:1px solid var(--line)}
@media(min-width:520px){
  .workspace{padding:0 14px 16px}
  .topbar{margin:0 -14px;padding-left:14px;padding-right:14px}
  .coverage-lines{display:none}
  table.coverage-table{display:table;width:100%;border-collapse:collapse;font-size:11px}
  table.coverage-table th,table.coverage-table td{padding:4px 6px;border-bottom:1px solid var(--line);text-align:left}
}
:host(.standalone) .app{display:grid;grid-template-columns:156px minmax(0,1fr)}
:host(.standalone) .rail{height:100vh;flex-direction:column;align-items:stretch;border-bottom:0;border-right:1px solid var(--line);padding:10px 6px;overflow:visible}
:host(.standalone) .brand{display:grid!important;gap:1px;padding:2px 8px 10px}
:host(.standalone) .brand-mark{font-size:12px}
:host(.standalone) .brand-sub{font-size:10px}
:host(.standalone) .rail-item{width:100%;text-align:left;border-bottom:0;border-left:2px solid transparent;border-radius:0;padding:6px 8px}
:host(.standalone) .rail-item.active{border-left-color:var(--accent);background:color-mix(in srgb,var(--accent) 10%,transparent)}
:host(.standalone) .workspace{max-width:920px;margin:0;padding:12px 20px 24px}
:host(.standalone) .topbar{position:sticky;top:0;margin:0 -20px;padding:7px 20px}
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
/** 证据视图没返回正文时，核心给出的具体原因（按 read_views 契约）。 */
export const EVIDENCE_OMITTED:Record<string,string>={
  redaction_unsafe:"这条证据未通过脱敏校验，只提供元数据，不返回正文。",
  object_unavailable:"对象仓不可用，暂时读取不到正文。",
  body_too_large:"正文超过 4MB，不在界面上直接展示，可导出证据包查看。",
  object_unreadable:"正文对象读取失败，可能已损坏或被占用。",
  object_corrupted:"正文对象与登记摘要不一致，已停止展示。",
  binary_content:"这条证据是二进制材料，不支持文本展示。",
};
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
