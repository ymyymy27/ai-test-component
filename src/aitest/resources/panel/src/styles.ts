export const styles = `
:host{
  --fg:var(--vscode-foreground,#e6e8ea);
  --muted:var(--vscode-descriptionForeground,#9aa3ad);
  --bg:var(--vscode-editor-background,#15191f);
  --panel:var(--vscode-sideBar-background,#181c22);
  --surface:var(--vscode-editorWidget-background,#1d2229);
  --line:var(--vscode-widget-border,rgba(255,255,255,.09));
  --accent:var(--vscode-focusBorder,#4c9aff);
  --ok:#5fd18b;--warn:#e4bd67;--bad:#ef8c7f;
  display:block;min-height:100vh;background:var(--bg);color:var(--fg);
  font:12px/1.5 var(--vscode-font-family,system-ui,-apple-system,"Segoe UI",sans-serif);
}
*{box-sizing:border-box}
button,input,textarea,select{font:inherit;color:inherit}
button{border:1px solid var(--line);background:var(--surface);border-radius:4px;padding:4px 8px;min-height:26px;cursor:pointer}
button:hover:not(:disabled){background:var(--vscode-list-hoverBackground,#252c35)}
button:disabled{opacity:.45;cursor:not-allowed}
button.primary{background:var(--accent);border-color:var(--accent);color:var(--vscode-button-foreground,#fff);font-weight:600}
button.link{background:transparent;border:0;color:var(--accent);padding:2px 0;min-height:0}
input,textarea,select{display:block;width:100%;padding:5px 7px;background:var(--vscode-input-background,#11151b);border:1px solid var(--vscode-input-border,var(--line));border-radius:4px;min-height:28px}
textarea{resize:vertical;min-height:70px}
input[type=checkbox]{display:inline-block;width:auto;min-height:0;margin:0 6px 0 0;vertical-align:middle}
label{display:block;margin:7px 0}
label>span{display:block;font-size:11px;color:var(--muted);margin-bottom:3px}
button:focus-visible,input:focus-visible,textarea:focus-visible,select:focus-visible,summary:focus-visible{outline:2px solid color-mix(in srgb,var(--accent) 65%,transparent);outline-offset:1px}
h1,h2,h3,p{margin:0 0 6px}
h1{font-size:16px;font-weight:650}h2{font-size:14px;font-weight:600}h3{font-size:12px;font-weight:600}
.app{display:block}
.tabs{display:flex;height:34px;padding:0 6px;background:var(--panel);border-bottom:1px solid var(--line);position:sticky;top:0;z-index:20;overflow-x:auto;scrollbar-width:none}
.tabs::-webkit-scrollbar{display:none}
.tab{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;color:var(--muted);padding:8px 9px;min-height:34px;white-space:nowrap}
.tab.active{border-bottom-color:var(--accent);color:var(--fg);font-weight:600}
.main{max-width:920px;margin:0 auto;padding:0 10px 18px}
.project{display:flex;align-items:center;gap:6px;padding:7px 0;border-bottom:1px solid var(--line)}
.project .dot{width:7px;height:7px;border-radius:50%;background:var(--muted)}
.project .dot.ready{background:var(--ok)}
.project .name{font-weight:600;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex:1}
.project .meta{font-size:10px;color:var(--muted)}
.actions{display:flex;gap:5px;flex-wrap:wrap;align-items:center;margin:6px 0}
.section{border-bottom:1px solid var(--line);padding:9px 0}
.section:last-child{border-bottom:0}
.section h2{margin:0 0 6px}
.section h3{color:var(--muted);margin:0 0 6px;text-transform:uppercase;letter-spacing:.08em;font-size:10px}
.muted{color:var(--muted)}.subtle{font-size:11px}
.badge{display:inline-block;padding:1px 7px;border-radius:999px;border:1px solid var(--line);font-size:10px;font-weight:600;color:var(--muted)}
.badge.ok{color:var(--ok);border-color:color-mix(in srgb,var(--ok) 40%,var(--line))}
.badge.warn{color:var(--warn);border-color:color-mix(in srgb,var(--warn) 40%,var(--line))}
.badge.bad{color:var(--bad);border-color:color-mix(in srgb,var(--bad) 40%,var(--line))}
.notice{border-left:2px solid var(--accent);padding:5px 8px;margin:6px 0;color:var(--fg)}
.gap{border-left:2px solid var(--warn);padding:5px 8px;margin:6px 0}
.rows{display:grid;gap:2px}
.row{display:flex;gap:8px;align-items:center;padding:5px 6px;border-radius:4px;min-width:0}
.row:hover{background:var(--vscode-list-hoverBackground,#252c35)}
.row .grow{flex:1;min-width:0;overflow-wrap:anywhere}
.row button{min-height:0;padding:2px 5px}
.list{list-style:none;margin:0;padding:0}
.list li{padding:3px 0;border-bottom:1px solid color-mix(in srgb,var(--line) 55%,transparent)}
.list li:last-child{border-bottom:0}
.split{display:grid;grid-template-columns:repeat(3,minmax(0,1fr));gap:8px;margin:6px 0}
.metric{border:1px solid var(--line);border-radius:4px;padding:6px 8px}
.metric b{display:block;font-size:15px;line-height:1.2}.metric span{font-size:10px;color:var(--muted)}
.steps{display:flex;flex-direction:column;gap:2px}
.step{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:6px;align-items:center;padding:6px;border:1px solid transparent;border-radius:4px;cursor:pointer}
.step:hover{background:var(--vscode-list-hoverBackground,#252c35)}
.step.active{border-color:color-mix(in srgb,var(--accent) 55%,var(--line));background:color-mix(in srgb,var(--accent) 8%,transparent)}
.step .step-title{font-weight:600;font-size:12px;overflow-wrap:anywhere}
.step .step-meta{display:block;font-size:10px;color:var(--muted);margin-top:1px}
.facts{display:grid;grid-template-columns:auto 1fr;gap:3px 10px;margin:4px 0}
.facts dt{color:var(--muted);font-size:10px}.facts dd{margin:0;overflow-wrap:anywhere}
.table{width:100%;border-collapse:collapse;font-size:11px}
.table th,.table td{text-align:left;padding:4px 5px;border-bottom:1px solid var(--line)}
.table th{color:var(--muted);font-weight:600}
.table td:last-child,.table th:last-child{text-align:right}
pre{white-space:pre-wrap;overflow-wrap:anywhere;max-height:320px;overflow:auto;background:var(--vscode-textCodeBlock-background,#11151b);border:1px solid var(--line);border-radius:4px;padding:8px;font-size:11px}
details{border:1px solid var(--line);border-radius:4px;padding:0 8px;margin:6px 0}
details summary{cursor:pointer;padding:6px 0;color:var(--muted);font-size:11px}
details[open]{padding-bottom:8px}
.tabs-inline{display:flex;gap:0;border-bottom:1px solid var(--line);margin:6px 0}
.tabs-inline button{border:0;border-bottom:2px solid transparent;border-radius:0;background:transparent;padding:5px 8px;color:var(--muted)}
.tabs-inline button.active{border-bottom-color:var(--accent);color:var(--fg)}
.status{font-size:10px;color:var(--muted);padding:6px 0;border-top:1px solid var(--line);margin-top:6px}
@media(min-width:560px){.main{padding:0 14px 20px}.table{font-size:11px}}
:host(.standalone) .app{display:grid;grid-template-columns:150px minmax(0,1fr)}
:host(.standalone) .tabs{height:100vh;flex-direction:column;border-bottom:0;border-right:1px solid var(--line);padding:10px 6px;align-items:stretch}
:host(.standalone) .tab{border-bottom:0;border-left:2px solid transparent;text-align:left;padding:6px 8px}
:host(.standalone) .tab.active{border-left-color:var(--accent);background:color-mix(in srgb,var(--accent) 9%,transparent)}
:host(.standalone) .main{max-width:960px;margin:0;padding:14px 20px 24px}
`;
