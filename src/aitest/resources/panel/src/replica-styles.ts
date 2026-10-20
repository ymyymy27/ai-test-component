export const sidebarCss = String.raw`
:host { display:block; height:100%; min-height:0; background:#22252b; color:#dce0e8;
  font:12px/1.35 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif; }
* { box-sizing:border-box; }
button,input,select,textarea { font:inherit; color:inherit; }
button { border:0; background:none; cursor:pointer; }
button:focus-visible,input:focus-visible,select:focus-visible,textarea:focus-visible { outline:1px solid #3679d4; outline-offset:-1px; }
.sidebar { height:100%; display:flex; flex-direction:column; min-height:0; background:#22252b; }
.top { flex:none; height:110px; position:relative; }
.brand-row { height:43px; display:flex; align-items:center; padding:0 14px; gap:8px; }
.brand-title { padding:0; border:0; background:transparent; font-size:14px; line-height:1; font-weight:600; color:#dce0e8; cursor:pointer; }
.brand-title:hover { color:#fff; }
.popover { position:absolute; z-index:12; width:230px; padding:5px; border:1px solid #414753; border-radius:4px; background:#292d34; box-shadow:0 7px 20px rgba(0,0,0,.38); }
.popover[hidden] { display:none; }
.popover button { display:flex; align-items:center; width:100%; min-height:27px; padding:3px 8px; border-radius:3px; color:#dce0e8; text-align:left; }
.popover button:hover,.popover button.selected { background:#343944; }
.popover button span { margin-left:auto; color:#86b7ff; }
.popover-head { padding:4px 8px 5px; color:#8992a2; font-size:10.5px; }
.more-popover { top:34px; right:10px; width:170px; }
.project-popover { top:73px; left:12px; }
.brand-spacer { flex:1; }
.icon-btn { width:24px; height:24px; display:grid; place-items:center; color:#a0a8b6; border-radius:3px; }
.icon-btn:hover { background:#2b3038; color:#dce0e8; }
.icon-btn svg { width:15px; height:15px; }
.project-row,.model-row { height:34px; display:flex; align-items:center; padding:0 14px; gap:8px; }
.project-row { cursor:pointer; }
.project-row:hover,.model-row:hover { background:#252931; }
.project-row svg,.model-row svg { width:17px; height:17px; color:#9fb1cf; flex:none; }
.project-name { font-size:14px; font-weight:600; color:#e3e6ed; white-space:nowrap; }
.caret { width:12px; color:#a0a8b6; font-size:11px; }
.project-tail { margin-left:auto; color:#a0a8b6; font-size:11px; }
.model-name { color:#a8afbc; white-space:nowrap; }
.model-tail { margin-left:auto; color:#79aaff; font-size:11px; }
.content { flex:1; min-height:0; overflow-y:auto; overflow-x:hidden; scrollbar-width:thin; scrollbar-color:#3a3f49 transparent; }
.section { border-top:1px solid #2b2f37; }
.section.current { border-top:0; }
.section.pending.accent,.section.plan.accent { box-shadow:inset 2px 0 0 #4a74c8; }
.section-head { height:36px; display:flex; align-items:center; width:100%; padding:0 14px; color:#dce0e8; text-align:left; }
.section-head:hover { background:#252931; }
.section-chevron { width:13px; color:#7e8796; font-size:10px; }
.section-title { font-size:12px; font-weight:600; }
.section-tail { margin-left:auto; color:#8f98a8; font-size:11px; }
.section-tail.success { color:#41b36a; }
.section-tail.warning { color:#d6a84d; }
.body { padding:0 14px 12px; }
.test-title { margin:5px 0 1px; color:#e3e6ed; font-size:14px; line-height:1.35; font-weight:600; }
.test-meta { margin:0 0 9px; color:#9fa7b5; font-size:11.5px; }
.status-line { display:flex; align-items:center; gap:5px; margin:4px 0 9px; color:#e0b75d; font-size:12px; }
.status-line.info { color:#86b7ff; }
.status-line.neutral { color:#a7afbc; }
.status-line .icon { width:12px; height:12px; }
.button-row { display:flex; align-items:center; gap:8px; margin:7px 0 8px; }
.btn { min-height:30px; padding:0 11px; display:inline-flex; align-items:center; justify-content:center; gap:6px;
  border:1px solid #414753; border-radius:3px; color:#dce0e8; background:#292d34; font-size:12px; font-weight:600; white-space:nowrap; }
.btn:hover { background:#313640; }
.btn.primary { border-color:#3679d4; background:#3679d4; color:#f5f8ff; }
.btn.primary:hover { background:#3e86ea; }
.btn.ghost { background:transparent; border-color:#3a404a; }
.btn.small { min-height:26px; padding:0 9px; font-size:11.5px; }
.btn.wide { width:100%; }
.btn svg { width:13px; height:13px; }
.link { display:inline-block; padding:0; color:#86b7ff; font-size:11.5px; line-height:1.5; text-align:left; }
.link:hover { text-decoration:underline; }
.link.block { display:block; margin:5px 0 7px; }
.kv { display:grid; grid-template-columns:58px minmax(0,1fr); column-gap:8px; margin:7px 0; }
.kv-label { color:#8992a2; }
.kv-value { min-width:0; color:#c8cdd7; overflow-wrap:anywhere; }
.kv-value strong { color:#dce0e8; font-weight:500; }
.note-row { display:flex; align-items:flex-start; gap:6px; margin:6px 0; color:#8992a2; font-size:11px; line-height:1.45; }
.note-row svg { flex:none; width:12px; height:12px; margin-top:1px; }
.divider { height:1px; margin:9px 0; background:#2b2f37; }
.progress-row { display:flex; align-items:center; gap:8px; margin:7px 0 5px; }
.progress-label { color:#dce0e8; }
.progress-tail { margin-left:auto; color:#a0a8b6; }
.progress-track { height:2px; background:#343944; overflow:hidden; }
.progress-bar { width:60%; height:100%; background:#4b74cb; }
.pending-item { display:flex; gap:9px; padding:8px 0 6px; }
.pending-item > svg { width:15px; height:15px; flex:none; color:#a0a8b6; margin-top:1px; }
.pending-main { min-width:0; }
.pending-title { color:#dce0e8; font-size:12px; font-weight:500; }
.pending-detail { margin-top:1px; color:#8992a2; font-size:11px; line-height:1.4; }
.metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:7px 8px; margin:8px 0; }
.metric { color:#a0a8b6; font-size:11px; line-height:1.35; }
.metric strong { display:block; color:#dce0e8; font-size:12px; font-weight:500; }
.metric.warn strong { color:#e0b75d; }
.metric.danger strong { color:#ef7d72; }
.plan-top { display:flex; align-items:center; gap:7px; margin:4px 0 8px; }
.plan-top strong { font-weight:500; color:#dce0e8; }
.plan-top .link { margin-left:auto; }
.tree-root { display:flex; align-items:center; gap:6px; margin:5px 0 4px; color:#dce0e8; }
.tree-root .chevron { color:#9aa3b2; font-size:10px; }
.tree-count { margin-left:auto; color:#8992a2; font-size:10.5px; }
.tree-item { display:flex; align-items:center; gap:7px; width:100%; min-height:23px; padding:0 0 0 23px; border:0; background:transparent; color:#b8bec9; font-size:11.5px; text-align:left; cursor:pointer; }
.tree-item.history { color:#737c8c; }
.tree-item.selected { background:#343944; }
.tree-circle { width:11px; height:11px; border:1px solid #6f7787; border-radius:50%; flex:none; }
.tree-item.done .tree-circle { border-color:#75a6f2; background:#315d9d; box-shadow:inset 0 0 0 2px #343944; }
.tree-state { margin-left:auto; color:#8992a2; font-size:10px; }
.records-collapsed { display:flex; align-items:center; width:100%; height:36px; padding:0 14px; border-top:1px solid #2b2f37; color:#dce0e8; }
.records-collapsed .section-title { margin-left:0; }
.records-collapsed .section-tail { margin-left:auto; }
.footer { flex:none; height:30px; display:flex; align-items:center; gap:6px; padding:0 14px; border-top:1px solid #2b2f37; color:#8992a2; font-size:10.5px; }
.footer .dot { width:6px; height:6px; border-radius:50%; background:#36a85b; }
.settings-top { display:flex; align-items:center; gap:7px; padding:0 14px 8px; color:#86b7ff; font-size:11.5px; }
.settings-title { margin:1px 14px 2px; color:#e3e6ed; font-size:15px; font-weight:600; }
.settings-sub { margin:0 14px 13px; color:#8992a2; font-size:11px; }
.settings-body { padding:0 14px 12px; }
.check-row { display:flex; align-items:flex-start; gap:8px; margin:10px 0; color:#c8cdd7; }
.check-row input { width:13px; height:13px; margin:1px 0 0; accent-color:#3679d4; }
.field-label { display:block; margin:10px 0 4px; color:#cfd4de; font-size:11.5px; }
.input { width:100%; min-height:30px; padding:4px 8px; border:1px solid #414753; border-radius:3px; background:#292d34; color:#dce0e8; }
.input:disabled { opacity:.72; }
.hint { margin:4px 0 8px; color:#8992a2; font-size:10.5px; line-height:1.4; }
.settings-actions { display:flex; gap:9px; margin:15px 0 13px; }
.settings-section { margin-top:14px; padding-top:11px; border-top:1px solid #2b2f37; }
.settings-section h3 { margin:0 0 6px; color:#dce0e8; font-size:12px; font-weight:500; }
.settings-section p { margin:5px 0; color:#8992a2; font-size:11px; line-height:1.5; }
details summary { cursor:pointer; color:#a0a8b6; list-style:none; }
details summary::-webkit-details-marker { display:none; }
.sr-only { position:absolute; width:1px; height:1px; overflow:hidden; clip:rect(0,0,0,0); }
@media (max-width:240px){ .kv{grid-template-columns:48px 1fr}.button-row{flex-wrap:wrap}.metrics{grid-template-columns:1fr 1fr} }
`;

export const detailCss = String.raw`
:host { display:block; height:100%; min-height:0; background:#191b20; color:#dce0e8;
  font:12px/1.4 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif; }
* { box-sizing:border-box; }
button,input,select,textarea { font:inherit; color:inherit; }
button { border:0; background:none; cursor:pointer; }
.breadcrumb { flex:none; height:27px; display:flex; align-items:center; padding:0 12px; border-bottom:1px solid #24272d;
  color:#8992a2; font-size:10px; white-space:nowrap; overflow:hidden; }
.breadcrumb span { overflow:hidden; text-overflow:ellipsis; }
.detail { height:100%; display:flex; flex-direction:column; min-height:0; background:#191b20; }
.scroller { flex:1; min-height:0; overflow:auto; padding:20px 24px 32px; scrollbar-width:thin; scrollbar-color:#383d46 transparent; }
h1 { margin:0 0 4px; font-size:20px; line-height:1.25; font-weight:600; color:#dce0e8; letter-spacing:.1px; }
h2 { margin:16px 0 8px; font-size:13px; line-height:1.4; font-weight:600; color:#dce0e8; }
.subtle { color:#8992a2; font-size:11.5px; line-height:1.55; }
.lead { margin:2px 0 13px; color:#a0a8b6; font-size:12px; line-height:1.55; }
.divider { height:1px; margin:13px 0; background:#2b2f37; }
.table { width:100%; border-collapse:collapse; table-layout:fixed; }
.table th { padding:9px 0; border-top:1px solid #2e323a; border-bottom:1px solid #2e323a; color:#a0a8b6; font-size:11px; font-weight:400; text-align:left; }
.table td { padding:11px 0; border-bottom:1px solid #2b2f37; vertical-align:top; color:#c8cdd7; font-size:11.5px; }
.table th+th,.table td+td { padding-left:16px; }
.table .muted { color:#8992a2; }
.table strong { color:#dce0e8; font-weight:500; }
.banner { margin:12px 0; padding:9px 10px; border-left:2px solid #7ea6f5; background:#24272e; color:#b4bac5; line-height:1.55; }
.banner strong { display:block; margin-bottom:2px; color:#dce0e8; font-weight:500; }
.banner.warning { border-left-color:#d4a548; }
.banner.danger { border-left-color:#d66e69; }
.button-row { display:flex; align-items:center; gap:9px; margin:14px 0 12px; flex-wrap:wrap; }
.btn { min-height:30px; padding:0 11px; display:inline-flex; align-items:center; justify-content:center; gap:6px;
  border:1px solid #414753; border-radius:3px; background:#292d34; color:#dce0e8; font-size:11.5px; font-weight:500; white-space:nowrap; }
.btn:hover { background:#313640; }
.btn.primary { border-color:#3679d4; background:#3679d4; color:#f5f8ff; }
.btn.primary:hover { background:#3e86ea; }
.btn svg { width:13px; height:13px; }
.link { color:#86b7ff; font-size:11.5px; }
.link:hover { text-decoration:underline; }
.input { width:100%; min-height:31px; padding:5px 8px; border:1px solid #414753; border-radius:3px; background:#292d34; color:#dce0e8; }
textarea.input { min-height:64px; resize:vertical; }
.field-label { display:block; margin:12px 0 5px; color:#dce0e8; font-size:11.5px; }
.field-note { margin:4px 0 10px; color:#8992a2; font-size:10.5px; }
.status-main { display:flex; align-items:center; gap:8px; margin:4px 0 3px; }
.status-main .outcome { color:#d6a84d; font-size:22px; font-weight:600; }
.status-main .grade { color:#dce0e8; font-size:13px; }
.status-main .grade::before { content:""; display:inline-block; width:1px; height:18px; margin:0 10px 0 0; background:#3a3f49; vertical-align:-3px; }
.summary-text { margin:3px 0 0; color:#a0a8b6; font-size:11.5px; }
.heading-row { display:flex; align-items:flex-start; gap:14px; }
.heading-row .grow { flex:1; min-width:0; }
.small-status { margin-top:12px; color:#d6a84d; white-space:nowrap; font-size:11.5px; }
.large-metrics { display:grid; grid-template-columns:repeat(3,minmax(0,1fr)); gap:16px; margin:17px 0 4px; }
.large-metric { color:#a0a8b6; font-size:11px; }
.large-metric strong { display:block; margin-top:3px; color:#dce0e8; font-size:21px; line-height:1.15; font-weight:500; }
.detail-list { margin:12px 0; }
.detail-list dt { float:left; width:108px; clear:left; color:#8992a2; }
.detail-list dd { margin:0 0 8px 108px; color:#c8cdd7; }
.tabs { display:flex; gap:22px; margin:12px 0 20px; border-bottom:1px solid #2b2f37; }
.tabs button { position:relative; padding:0 1px 9px; color:#8992a2; font-size:11.5px; }
.tabs button[aria-selected="true"] { color:#dce0e8; }
.tabs button[aria-selected="true"]::after { position:absolute; right:0; bottom:-1px; left:0; height:2px; content:""; background:#5f8fe8; }
.radio-line { display:flex; align-items:center; gap:8px; margin:8px 0; color:#c8cdd7; }
.radio-line input { width:13px; height:13px; margin:0; accent-color:#3679d4; }
.form-card { margin-top:10px; }
.checkbox-line { display:flex; align-items:center; gap:8px; margin:12px 0; color:#c8cdd7; }
.checkbox-line input { width:13px; height:13px; margin:0; accent-color:#3679d4; }
.inline-fields { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.mono { font-family:"Cascadia Code","Consolas",monospace; font-size:10.5px; }
.empty-detail { margin-top:20px; color:#8992a2; }
@media(max-width:620px){ .scroller{padding:16px}.table{font-size:11px}.large-metrics{gap:8px}.detail-list dt{width:82px}.detail-list dd{margin-left:82px}.inline-fields{grid-template-columns:1fr} }
`;
export const mockupCss = String.raw`
:host { display:block; width:1024px; height:1110px; background:#191b20; color:#dce0e8;
  font:12px/1.35 "Segoe UI","Microsoft YaHei UI","Microsoft YaHei",sans-serif; }
* { box-sizing:border-box; }
button { font:inherit; color:inherit; }
.app { width:1024px; height:1110px; display:flex; flex-direction:column; overflow:hidden; background:#191b20;
  border:1px solid #343944; border-radius:7px; }
.titlebar { position:relative; z-index:20; flex:none; height:35px; display:flex; align-items:center; background:#272a31; border-bottom:1px solid #30343b; }
.menu-items { display:flex; align-items:center; gap:2px; padding-left:8px; color:#dce0e8; font-size:12px; }
.menu-wrap { position:relative; }
.menu-button { min-height:28px; padding:0 9px; border-radius:3px; color:#dce0e8; }
.menu-button:hover,.menu-button.active { background:#343944; }
.menu-dropdown { position:absolute; top:31px; left:0; z-index:30; width:190px; padding:5px; border:1px solid #414753; border-radius:4px; background:#292d34; box-shadow:0 8px 24px rgba(0,0,0,.4); }
.menu-dropdown button { display:block; width:100%; min-height:26px; padding:3px 8px; border-radius:3px; color:#dce0e8; text-align:left; }
.menu-dropdown button:hover { background:#343944; }
.status-action { color:#d6a84d; }
.window-title { position:absolute; left:50%; transform:translateX(-50%); color:#a0a8b6; font-size:11px; }
.scene-label { margin-left:auto; padding-right:12px; color:#8992a2; font-size:10px; }
.workspace { flex:1; min-height:0; display:grid; grid-template-columns:39px 300px minmax(0,1fr); }
.activitybar { display:flex; flex-direction:column; align-items:center; gap:2px; padding-top:5px; background:#272a31; border-right:1px solid #30343b; }
.activitybar button { position:relative; width:38px; height:34px; display:grid; place-items:center; color:#a0a8b6; }
.activitybar button:hover { color:#dce0e8; }
.activitybar button.active { color:#dce0e8; }
.activitybar button.active::before { position:absolute; top:0; bottom:0; left:0; width:2px; content:""; background:#3679d4; }
.activitybar svg { width:19px; height:19px; }
ai-test-panel { display:block; width:300px; height:100%; min-width:0; overflow:hidden; }
.activity-panel { width:300px; min-width:0; padding:16px 14px; background:#22252b; border-right:1px solid #2b2f37; }
.activity-panel h2 { margin:0 0 8px; color:#dce0e8; font-size:12px; font-weight:600; }
.activity-panel p { margin:0 0 14px; color:#8992a2; font-size:11px; line-height:1.55; }
.activity-panel button { width:100%; padding:7px 8px; border-radius:3px; color:#c8cdd7; text-align:left; }
.activity-panel button:hover,.activity-panel button.selected { background:#343944; color:#dce0e8; }
.editor { display:flex; flex-direction:column; min-width:0; min-height:0; background:#191b20; }
.tabbar { flex:none; height:44px; display:flex; align-items:flex-end; gap:2px; padding:0 9px; background:#272a31; border-bottom:1px solid #30343b; }
.tab { height:37px; max-width:230px; display:flex; align-items:center; gap:7px; padding:0 12px; color:#a0a8b6; background:#24272d; border-right:1px solid #30343b; border-top:1px solid transparent; font-size:12px; }
.tab.active { color:#e3e6ed; background:#191b20; border-top-color:#191b20; }
.tab svg { width:15px; height:15px; flex:none; color:#a7b1c0; }
.tab span:nth-child(2) { overflow:hidden; text-overflow:ellipsis; white-space:nowrap; }
.detail-tab { max-width:270px; }
.tab-close { margin-left:5px; color:#7d8492; font-size:13px; }
.editor-main { flex:1; min-height:0; overflow:hidden; }
.source-breadcrumb { height:27px; display:flex; align-items:center; padding:0 22px; border-bottom:1px solid #24272d; color:#8992a2; font-size:10px; }
.source-code { padding:8px 0 0; font:12px/18px "Cascadia Code","Consolas",monospace; }
.source-line { display:flex; min-height:18px; }
.source-line.active { background:#292e36; }
.line-no { width:52px; padding-right:15px; color:#596172; text-align:right; user-select:none; }
.line-text { color:#d3d7df; white-space:pre; }
.terminal { flex:none; height:57px; display:flex; flex-direction:column; border-top:1px solid #30343b; background:#191b20; }
.terminal-tabs { height:27px; display:flex; align-items:center; gap:17px; padding:0 38px; color:#8992a2; font-size:10px; }
.terminal-tabs button.active { color:#dce0e8; }
.terminal-note { margin-left:auto; color:#6f7786; }
.terminal pre { margin:0; padding:1px 38px 0; color:#a9b0bc; font:10px/13px "Cascadia Code","Consolas",monospace; }
.statusbar { flex:none; height:14px; display:flex; align-items:center; gap:18px; padding:0 8px; background:#272a31; color:#a9b0bc; font-size:10px; }
.status-spacer { flex:1; }
.branch { color:#dce0e8; }
ai-test-detail { display:block; width:100%; height:100%; }
`;
