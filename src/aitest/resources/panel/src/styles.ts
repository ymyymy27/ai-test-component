/** Editor-native design system: Trae/VS Code tokens, calm surfaces, one primary action per screen. */

export const shellStyles = `
:host{
  --bg:var(--vscode-sideBar-background,#171a1f);
  --surface:var(--vscode-editorWidget-background,#1f242c);
  --surface-2:color-mix(in srgb,var(--surface) 78%,var(--bg));
  --surface-3:color-mix(in srgb,var(--surface) 58%,var(--bg));
  --border:var(--vscode-panel-border,rgba(255,255,255,.11));
  --border-strong:color-mix(in srgb,var(--border) 62%,var(--vscode-foreground,#fff) 12%);
  --text:var(--vscode-foreground,#d7dbe2);
  --muted:var(--vscode-descriptionForeground,#9aa3af);
  --accent:var(--vscode-focusBorder,#4c9fed);
  --accent-strong:var(--vscode-button-background,#2f83d6);
  --accent-soft:color-mix(in srgb,var(--accent) 15%,transparent);
  --success:#3fb950;
  --success-soft:color-mix(in srgb,var(--success) 14%,transparent);
  --warning:#d7a23a;
  --warning-soft:color-mix(in srgb,var(--warning) 14%,transparent);
  --danger:#f85149;
  --danger-soft:color-mix(in srgb,var(--danger) 14%,transparent);
  --info:#58a6ff;
  --info-soft:color-mix(in srgb,var(--info) 14%,transparent);
  --radius:12px;
  --radius-sm:8px;
  --shadow:0 1px 0 rgba(255,255,255,.035),0 8px 24px rgba(0,0,0,.10);
  display:block;min-height:100vh;background:var(--bg);color:var(--text);
  font:13px/1.55 var(--vscode-font-family,system-ui,-apple-system,"Segoe UI","Microsoft YaHei",sans-serif);
  color-scheme:dark light;
}
*{box-sizing:border-box}
button,input,textarea,select{font:inherit;color:inherit}
button{cursor:pointer}
h1,h2,h3,h4,p,dl,dd{margin:0}
ul,ol{margin:0;padding:0;list-style:none}
svg{flex:none}
[hidden]{display:none!important}

.app{min-height:100vh;display:flex;flex-direction:column}
.skip{position:absolute;left:-9999px;top:0;background:var(--surface);padding:8px 12px;z-index:100}
.skip:focus{left:8px;top:8px}

.topbar{
  position:sticky;top:0;z-index:20;display:flex;align-items:center;gap:10px;
  padding:10px 14px;background:color-mix(in srgb,var(--bg) 92%,transparent);
  backdrop-filter:blur(14px);border-bottom:1px solid var(--border);
}
.brand{display:flex;align-items:center;gap:9px;min-width:0;flex:1}
.brand-mark{
  width:28px;height:28px;border-radius:9px;display:grid;place-items:center;color:#fff;
  background:linear-gradient(135deg,var(--accent-strong),#7c5cff);
  box-shadow:0 4px 14px color-mix(in srgb,var(--accent) 28%,transparent);
}
.brand-copy{min-width:0}
.brand-title{font-size:13px;font-weight:650;letter-spacing:.01em;white-space:nowrap}
.brand-sub{font-size:11px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis;max-width:210px}
.top-actions{display:flex;align-items:center;gap:4px}

.nav{
  display:flex;gap:2px;padding:5px 8px;overflow-x:auto;scrollbar-width:none;
  border-bottom:1px solid var(--border);background:color-mix(in srgb,var(--bg) 96%,transparent);
  position:sticky;top:49px;z-index:19;
}
.nav::-webkit-scrollbar{display:none}
.nav-btn{
  display:inline-flex;align-items:center;gap:5px;border:0;background:transparent;color:var(--muted);
  min-height:34px;padding:0 9px;border-radius:8px;font-size:11.5px;white-space:nowrap;
  transition:background .15s ease,color .15s ease;
}
@media(max-width:440px){
  .nav{padding:5px 6px}
  .nav-btn{gap:0;padding:0 7px;font-size:11px}
  .nav-btn svg{display:none}
}
.nav-btn:hover{background:var(--surface-2);color:var(--text)}
.nav-btn.active{background:var(--accent-soft);color:var(--text);font-weight:600}
.nav-btn.active svg{color:var(--accent)}

.content{width:100%;max-width:1000px;margin:0 auto;padding:14px;flex:1;min-width:0}
.footer{
  display:flex;align-items:center;justify-content:space-between;gap:10px;
  padding:9px 14px;border-top:1px solid var(--border);color:var(--muted);font-size:11px;
}
.footer-status{display:inline-flex;align-items:center;gap:6px;min-width:0}
.dot{width:7px;height:7px;border-radius:50%;background:var(--muted);flex:none}
.dot[data-state=ready]{background:var(--success);box-shadow:0 0 0 3px var(--success-soft)}
.dot[data-state=connecting]{background:var(--warning);box-shadow:0 0 0 3px var(--warning-soft)}
.dot[data-state=unavailable]{background:var(--danger);box-shadow:0 0 0 3px var(--danger-soft)}

@media(min-width:760px){
  .app{display:grid;grid-template-columns:204px minmax(0,1fr);grid-template-rows:auto minmax(0,1fr) auto}
  .topbar{grid-column:1/-1}
  .nav{
    grid-column:1;grid-row:2;flex-direction:column;align-items:stretch;gap:3px;
    padding:14px 10px;border-bottom:0;border-right:1px solid var(--border);
    position:sticky;top:49px;height:calc(100vh - 49px);overflow:auto;
  }
  .nav-btn{width:100%;justify-content:flex-start;padding:0 11px;min-height:36px}
  .content{grid-column:2;grid-row:2;padding:22px 26px}
  .footer{grid-column:2;grid-row:3}
}

.page{display:flex;flex-direction:column;gap:12px;min-width:0}
.page-head{display:flex;align-items:flex-start;gap:12px;justify-content:space-between;flex-wrap:wrap;margin-bottom:2px}
.page-head-copy{min-width:0;flex:1}
.eyebrow{font-size:10.5px;letter-spacing:.09em;text-transform:uppercase;color:var(--muted);font-weight:650;margin-bottom:5px}
.page-title{font-size:20px;line-height:1.25;font-weight:680;letter-spacing:-.015em}
.page-sub{font-size:12px;color:var(--muted);margin-top:4px;max-width:64ch}
h2{font-size:15px;font-weight:660;letter-spacing:-.01em}
h3{font-size:13px;font-weight:650}
h4{font-size:12px;font-weight:650}
p{font-size:12.5px}
.muted{color:var(--muted)}
.subtle{font-size:12px}
.tiny{font-size:11px}
.mono{font-family:var(--vscode-editor-font-family,ui-monospace,SFMono-Regular,Consolas,monospace);font-size:11px}
.wrap{overflow-wrap:anywhere;word-break:break-word}
.nowrap{white-space:nowrap}
.stack{display:flex;flex-direction:column;gap:12px}
.stack-sm{display:flex;flex-direction:column;gap:8px}
.inline{display:flex;align-items:center;gap:8px;flex-wrap:wrap}
.between{display:flex;align-items:center;justify-content:space-between;gap:10px}
.grow{flex:1;min-width:0}
.divider{height:1px;background:var(--border);margin:12px 0}

.card{
  background:linear-gradient(180deg,color-mix(in srgb,var(--surface) 97%,#fff 1%),var(--surface));
  border:1px solid var(--border);border-radius:var(--radius);padding:14px;
  box-shadow:var(--shadow);min-width:0;
}
.card.flat{box-shadow:none;background:var(--surface-2)}
.card.pad-lg{padding:18px}
.card.hero{padding:18px;position:relative;overflow:hidden}
.card.hero::after{
  content:"";position:absolute;right:-40px;top:-60px;width:200px;height:200px;
  background:radial-gradient(circle,var(--accent-soft),transparent 70%);pointer-events:none;
}
.card.hero > *{position:relative;z-index:1}
.card-head{display:flex;align-items:flex-start;justify-content:space-between;gap:10px;margin-bottom:10px}
.card-title{font-size:13px;font-weight:650}
.card-sub{font-size:11.5px;color:var(--muted);margin-top:2px}
.section-title{display:flex;align-items:center;gap:8px;font-size:12px;font-weight:650;color:var(--text);margin-bottom:8px}
.section-title svg{color:var(--muted)}

.btn{
  display:inline-flex;align-items:center;justify-content:center;gap:7px;
  min-height:32px;padding:0 12px;border-radius:8px;border:1px solid var(--border);
  background:var(--vscode-button-secondaryBackground,#2c313a);color:var(--text);
  font-size:12px;font-weight:550;transition:background .15s ease,border-color .15s ease,transform .15s ease,filter .15s ease;
}
.btn:hover:not(:disabled){background:color-mix(in srgb,var(--surface) 70%,#fff 6%);border-color:var(--border-strong);transform:translateY(-1px)}
.btn:active:not(:disabled){transform:translateY(0)}
.btn:focus-visible,.icon-btn:focus-visible,.nav-btn:focus-visible,.row:focus-visible{
  outline:2px solid var(--accent);outline-offset:2px;
}
.btn:disabled{opacity:.48;cursor:not-allowed}
.btn-primary{
  background:var(--accent-strong);border-color:transparent;color:var(--vscode-button-foreground,#fff);
  font-weight:650;box-shadow:0 5px 16px color-mix(in srgb,var(--accent) 25%,transparent);
}
.btn-primary:hover:not(:disabled){background:var(--vscode-button-hoverBackground,var(--accent-strong));filter:brightness(1.06)}
.btn-ghost{background:transparent;border-color:transparent;color:var(--accent);padding:0 8px}
.btn-ghost:hover:not(:disabled){background:var(--accent-soft);border-color:transparent}
.btn-danger{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 40%,var(--border));background:transparent}
.btn-danger:hover:not(:disabled){background:var(--danger-soft)}
.btn-sm{min-height:28px;padding:0 9px;font-size:11.5px}
.btn-block{width:100%}
.icon-btn{
  display:inline-grid;place-items:center;width:32px;height:32px;border-radius:8px;
  border:1px solid transparent;background:transparent;color:var(--muted);
}
.icon-btn:hover{background:var(--surface-2);color:var(--text);border-color:var(--border)}
.icon-btn:disabled{opacity:.4;cursor:not-allowed}

.chip{
  display:inline-flex;align-items:center;gap:5px;min-height:22px;padding:0 8px;
  border-radius:999px;border:1px solid var(--border);background:var(--surface-2);
  font-size:10.5px;font-weight:650;white-space:nowrap;
}
.chip[data-tone=info]{color:var(--info);border-color:color-mix(in srgb,var(--info) 38%,var(--border));background:var(--info-soft)}
.chip[data-tone=success]{color:var(--success);border-color:color-mix(in srgb,var(--success) 38%,var(--border));background:var(--success-soft)}
.chip[data-tone=warning]{color:var(--warning);border-color:color-mix(in srgb,var(--warning) 38%,var(--border));background:var(--warning-soft)}
.chip[data-tone=danger]{color:var(--danger);border-color:color-mix(in srgb,var(--danger) 38%,var(--border));background:var(--danger-soft)}
.chip[data-tone=neutral]{color:var(--muted)}

.banner{
  display:flex;align-items:flex-start;gap:9px;padding:10px 12px;margin:10px 0;
  border:1px solid var(--border);border-left-width:3px;border-radius:10px;background:var(--surface-2);
  font-size:12px;overflow-wrap:anywhere;
}
.banner svg{margin-top:1px}
.banner[data-tone=info]{border-left-color:var(--info);background:var(--info-soft)}
.banner[data-tone=success]{border-left-color:var(--success);background:var(--success-soft)}
.banner[data-tone=warning]{border-left-color:var(--warning);background:var(--warning-soft)}
.banner[data-tone=danger]{border-left-color:var(--danger);background:var(--danger-soft)}
.banner .grow{min-width:0}

.stepper{display:flex;margin:14px 0 2px;min-width:0}
.step{flex:1;position:relative;text-align:center;font-size:10px;color:var(--muted);min-width:0}
.step::before{content:"";position:absolute;top:11px;left:-50%;width:100%;height:1px;background:var(--border);z-index:0}
.step:first-child::before{display:none}
.step.done::before,.step.active::before{background:color-mix(in srgb,var(--accent) 55%,var(--border))}
.step-dot{
  position:relative;z-index:1;width:23px;height:23px;border-radius:50%;margin:0 auto 5px;
  display:grid;place-items:center;border:1px solid var(--border);background:var(--surface);
  font-size:10.5px;font-weight:700;color:var(--muted);
}
.step.done .step-dot{background:var(--success);border-color:transparent;color:#08150c}
.step.active .step-dot{background:var(--accent-strong);border-color:transparent;color:#fff;box-shadow:0 0 0 4px var(--accent-soft)}
.step.active{color:var(--text);font-weight:600}
.step-label{display:block;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}

.progress{height:6px;border-radius:999px;background:var(--surface-3);border:1px solid var(--border);overflow:hidden}
.progress > div{height:100%;border-radius:999px;background:linear-gradient(90deg,var(--accent-strong),#7c5cff);transition:width .3s ease}
.progress.slim{height:4px}

.stats{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:7px}
.stat{padding:9px 11px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);min-width:0}
.stat-value{font-size:18px;font-weight:680;letter-spacing:-.02em;line-height:1.2}
.stat-label{font-size:10.5px;color:var(--muted);margin-top:1px;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
@media(min-width:520px){.stats{grid-template-columns:repeat(4,minmax(0,1fr))}}

.field{display:block;margin:0 0 12px;min-width:0}
.field-label{display:block;font-size:11.5px;font-weight:650;margin-bottom:5px}
.field-help{display:block;font-size:11px;color:var(--muted);margin-top:4px;line-height:1.45}
.input,.textarea,.select{
  width:100%;min-height:34px;padding:7px 10px;border-radius:8px;border:1px solid var(--border);
  background:var(--vscode-input-background,#1b1f26);color:var(--text);outline:none;
  transition:border-color .15s ease,box-shadow .15s ease;
}
.textarea{min-height:86px;resize:vertical;line-height:1.5}
.input:focus,.textarea:focus,.select:focus{border-color:var(--accent);box-shadow:0 0 0 1px var(--accent)}
.input:disabled,.textarea:disabled,.select:disabled{opacity:.55;cursor:not-allowed}
.input[aria-invalid=true],.textarea[aria-invalid=true]{border-color:var(--danger)}
.check{display:flex;align-items:flex-start;gap:8px;font-size:12px;margin:10px 0;cursor:pointer}
.check input{margin:2px 0 0;accent-color:var(--accent-strong);flex:none}

.facts{display:grid;grid-template-columns:minmax(78px,auto) minmax(0,1fr);gap:7px 14px;margin:10px 0;font-size:12px}
.facts dt{color:var(--muted);font-size:11px}
.facts dd{margin:0;min-width:0;overflow-wrap:anywhere}

.list{display:flex;flex-direction:column;gap:6px}
.row{
  display:flex;align-items:center;gap:10px;width:100%;padding:10px 11px;text-align:left;
  border:1px solid var(--border);border-radius:10px;background:var(--surface-2);color:inherit;
  transition:background .15s ease,border-color .15s ease,transform .15s ease;
}
button.row:hover{background:color-mix(in srgb,var(--surface-2) 78%,#fff 5%);border-color:var(--border-strong);transform:translateY(-1px)}
.row.selected{border-color:color-mix(in srgb,var(--accent) 62%,var(--border));background:var(--accent-soft)}
.row-main{min-width:0;flex:1}
.row-title{font-size:12.5px;font-weight:600;overflow-wrap:anywhere}
.row-sub{font-size:11px;color:var(--muted);margin-top:1px;overflow-wrap:anywhere}
.row-tail{display:flex;align-items:center;gap:6px;flex:none}
.row-icon{width:28px;height:28px;border-radius:8px;display:grid;place-items:center;background:var(--surface-3);color:var(--muted);flex:none}

.empty{display:flex;flex-direction:column;align-items:center;text-align:center;gap:8px;padding:30px 16px;color:var(--muted)}
.empty-icon{width:44px;height:44px;border-radius:14px;display:grid;place-items:center;background:var(--surface-2);border:1px solid var(--border);color:var(--muted)}
.empty-title{font-size:13px;font-weight:650;color:var(--text)}
.empty-body{font-size:12px;max-width:46ch}

.search{position:relative;display:flex;align-items:center}
.search svg{position:absolute;left:9px;color:var(--muted);pointer-events:none}
.search .input{padding-left:30px}

.segmented{display:inline-flex;padding:3px;gap:2px;border:1px solid var(--border);border-radius:9px;background:var(--surface-3)}
.segmented button{border:0;background:transparent;color:var(--muted);border-radius:6px;padding:5px 10px;font-size:11.5px;font-weight:600}
.segmented button.active{background:var(--surface);color:var(--text);box-shadow:0 1px 3px rgba(0,0,0,.18)}
.subnav{align-self:flex-start;margin:0 0 2px}

.metric-row{display:flex;align-items:center;gap:10px;padding:8px 0;border-bottom:1px solid color-mix(in srgb,var(--border) 70%,transparent)}
.metric-row:last-child{border-bottom:0}
.metric-label{font-size:11.5px;color:var(--muted);width:92px;flex:none}
.metric-bar{flex:1;min-width:0}
.metric-value{font-size:11.5px;font-weight:650;min-width:52px;text-align:right;white-space:nowrap}

.step-tabs{display:flex;gap:5px;overflow-x:auto;padding:2px 0 8px;scrollbar-width:none}
.step-tabs::-webkit-scrollbar{display:none}
.step-tab{
  display:inline-flex;align-items:center;gap:5px;border:1px solid var(--border);background:var(--surface-2);
  color:var(--muted);border-radius:999px;min-height:26px;padding:0 9px;font-size:10.5px;font-weight:650;white-space:nowrap;
}
.step-tab.active{border-color:color-mix(in srgb,var(--accent) 65%,var(--border));background:var(--accent-soft);color:var(--text)}
.step-tab.done{color:var(--success);border-color:color-mix(in srgb,var(--success) 35%,var(--border))}
.workbench-grid{display:grid;gap:12px;min-width:0}
.step-list{display:grid;grid-template-columns:repeat(2,minmax(0,1fr));gap:6px;padding-bottom:4px}
.step-card{
  min-width:0;max-width:none;display:flex;align-items:flex-start;gap:8px;
  padding:9px 10px;border:1px solid var(--border);border-radius:10px;background:var(--surface-2);
  color:inherit;text-align:left;width:100%;
}
.step-card:hover{background:color-mix(in srgb,var(--surface-2) 76%,#fff 5%);border-color:var(--border-strong)}
.step-card.selected{border-color:color-mix(in srgb,var(--accent) 62%,var(--border));background:var(--accent-soft)}
.step-card-main{min-width:0;flex:1}
.step-card-title{font-size:11.5px;font-weight:650;white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.step-card-sub{font-size:10.5px;color:var(--muted);white-space:nowrap;overflow:hidden;text-overflow:ellipsis}
.step-dot-mini{width:8px;height:8px;border-radius:50%;background:var(--muted);flex:none;margin-top:5px}
.step-dot-mini[data-state=completed]{background:var(--success)}
.step-dot-mini[data-state=running]{background:var(--info)}
.step-dot-mini[data-state=blocked],.step-dot-mini[data-state=invalidated]{background:var(--danger)}
.step-dot-mini[data-state=pending_verification]{background:var(--warning)}
@media(min-width:760px){
  .workbench-grid{grid-template-columns:250px minmax(0,1fr);align-items:start}
  .step-list{grid-template-columns:1fr;align-content:start;overflow:visible;padding-bottom:0;position:sticky;top:64px;max-height:calc(100vh - 90px);overflow-y:auto}
  .step-card{min-width:0;max-width:none;width:100%}
}


.evidence{
  white-space:pre-wrap;overflow-wrap:anywhere;max-height:360px;overflow:auto;
  background:var(--vscode-textCodeBlock-background,#101318);border:1px solid var(--border);
  border-radius:9px;padding:12px;margin:10px 0;
  font-family:var(--vscode-editor-font-family,ui-monospace,SFMono-Regular,Consolas,monospace);
  font-size:11.5px;line-height:1.6;tab-size:2;user-select:text;
}
details.group{border:1px solid var(--border);border-radius:10px;background:var(--surface-2);margin:8px 0;overflow:hidden}
details.group > summary{cursor:pointer;padding:9px 11px;font-size:12px;font-weight:650;list-style:none;display:flex;align-items:center;gap:7px}
details.group > summary::-webkit-details-marker{display:none}
details.group > summary::before{content:"›";color:var(--muted);font-size:15px;line-height:1;transition:transform .15s ease;transform:rotate(0)}
details.group[open] > summary::before{transform:rotate(90deg)}
details.group > summary:hover{background:color-mix(in srgb,var(--surface-2) 75%,#fff 4%)}
details.group > div{padding:2px 11px 11px}

.overlay{
  position:fixed;inset:0;z-index:60;display:grid;place-items:center;padding:14px;
  background:rgba(0,0,0,.56);backdrop-filter:blur(2px);
}
.dialog{
  width:min(760px,100%);max-height:min(84vh,760px);overflow:auto;
  background:var(--surface);border:1px solid var(--border-strong);border-radius:14px;
  box-shadow:0 24px 70px rgba(0,0,0,.48);padding:0;
}
.dialog-head{
  display:flex;align-items:center;gap:10px;padding:13px 15px;border-bottom:1px solid var(--border);
  position:sticky;top:0;background:var(--surface);z-index:2;
}
.dialog-title{font-size:13.5px;font-weight:650;flex:1;min-width:0;overflow-wrap:anywhere}
.dialog-body{padding:14px 15px 16px}

.spinner{
  width:14px;height:14px;border-radius:50%;border:2px solid color-mix(in srgb,var(--accent) 30%,transparent);
  border-top-color:var(--accent);animation:spin .8s linear infinite;flex:none;
}
@keyframes spin{to{transform:rotate(360deg)}}
.loading-line{display:flex;align-items:center;gap:8px;color:var(--muted);font-size:12px;padding:8px 0}

.toast{
  position:fixed;left:50%;bottom:18px;transform:translateX(-50%);z-index:70;max-width:min(520px,92vw);
  display:flex;align-items:flex-start;gap:8px;padding:10px 13px;border-radius:10px;
  background:var(--surface);border:1px solid var(--border-strong);box-shadow:0 14px 40px rgba(0,0,0,.35);
  font-size:12px;
}
.toast[data-tone=success]{border-left:3px solid var(--success)}
.toast[data-tone=warning]{border-left:3px solid var(--warning)}
.toast[data-tone=danger]{border-left:3px solid var(--danger)}
.toast[data-tone=info]{border-left:3px solid var(--info)}

.hero-actions{display:flex;flex-wrap:wrap;gap:8px;margin-top:14px}
.hero-steps{display:flex;flex-direction:column;gap:7px;margin-top:14px}
.hero-step{display:flex;align-items:flex-start;gap:9px;font-size:12px;color:var(--muted)}
.hero-step b{color:var(--text);font-weight:650}
.hero-step-num{
  width:19px;height:19px;border-radius:50%;display:grid;place-items:center;flex:none;
  background:var(--surface-2);border:1px solid var(--border);font-size:10px;font-weight:700;color:var(--muted);
}
.hero-step.done .hero-step-num{background:var(--success-soft);border-color:color-mix(in srgb,var(--success) 40%,var(--border));color:var(--success)}
.hero-step.active .hero-step-num{background:var(--accent-soft);border-color:color-mix(in srgb,var(--accent) 45%,var(--border));color:var(--accent)}

.code-block{
  white-space:pre-wrap;overflow-wrap:anywhere;max-height:240px;overflow:auto;
  background:var(--vscode-textCodeBlock-background,#101318);border:1px solid var(--border);
  border-radius:8px;padding:10px;font-family:var(--vscode-editor-font-family,ui-monospace,monospace);
  font-size:11px;line-height:1.55;
}
.form-grid{display:grid;gap:0}
@media(min-width:620px){
  .form-grid{grid-template-columns:minmax(0,1fr) minmax(0,1fr);gap:0 14px}
  .form-grid .span-2{grid-column:1/-1}
  .content{padding:24px 30px}
}

@media(prefers-reduced-motion:reduce){
  *,*::before,*::after{animation-duration:.01ms!important;animation-iteration-count:1!important;transition-duration:.01ms!important}
  .btn:hover:not(:disabled),button.row:hover{transform:none}
}
`;
