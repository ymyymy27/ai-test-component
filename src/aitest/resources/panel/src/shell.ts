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
`;

export function text(tag:string, value:string, className=""):HTMLElement {
  const element=document.createElement(tag);element.textContent=value;element.className=className;return element;
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
