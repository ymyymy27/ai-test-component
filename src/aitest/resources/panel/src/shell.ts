/** Editor-scale shell. All business values are supplied by the core. */
export const shellStyles = `
:host{display:block;color:var(--vscode-foreground,#ccc);font:var(--vscode-font-size,13px)/1.5 var(--vscode-font-family,system-ui)}
*{box-sizing:border-box}main{padding:0 12px 16px;min-width:0}header{padding:10px 0;border-bottom:1px solid var(--vscode-panel-border,#555)}
h1,h2,h3,p{margin:0 0 6px}h1{font-size:14px}h2,h3{font-size:13px}section{padding:10px 0;min-width:0}
.toolbar,.actions,.details{display:flex;align-items:center;flex-wrap:wrap;gap:6px}.toolbar h1{flex:1;margin:0}
button,input,textarea,select{font:inherit;color:inherit;max-width:100%}button{border:1px solid transparent;padding:4px 8px;border-radius:2px;background:var(--vscode-button-secondaryBackground,#383838);cursor:pointer}
button.primary{background:var(--vscode-button-background,#007acc);color:var(--vscode-button-foreground,#fff)}button:disabled{opacity:.55;cursor:not-allowed}
input,textarea,select{display:block;width:100%;padding:5px 6px;background:var(--vscode-input-background,#303030);border:1px solid var(--vscode-input-border,#666);border-radius:2px}
textarea{resize:vertical;min-height:60px}label{display:block;margin:8px 0}button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:1px solid var(--vscode-focusBorder,#007acc);outline-offset:2px}
details.group{border-bottom:1px solid var(--vscode-panel-border,#555)}summary{cursor:pointer;padding:9px 0;font-weight:600}details.group>div{padding:0 0 10px}
.muted{color:var(--vscode-descriptionForeground,#aaa)}.scope{overflow-wrap:anywhere}.status{padding:6px 0;overflow-wrap:anywhere}.row{padding:6px 0;border-bottom:1px solid var(--vscode-panel-border,#555)}
ul{list-style:none;padding:0;margin:0}li{padding:4px 0}pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto}dd{margin:0 0 8px;overflow-wrap:anywhere}dt{color:var(--vscode-descriptionForeground,#aaa)}
.phase{padding:4px 0}.next{padding:6px 0}.actions{margin:8px 0}.record-id{overflow-wrap:anywhere}footer{padding-top:10px;font-size:12px}.diagnostics{margin-top:12px}
@media(prefers-reduced-motion:reduce){*{scroll-behavior:auto}}
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
