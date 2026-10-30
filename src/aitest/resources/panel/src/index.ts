/** Phase-one fact viewer and controlled command requester. The core owns
 * business outcomes and the independent human confirmation dialog. */
import {workflowActions, workflowCommand} from "./workflow";
import {Onboarding} from "./onboarding";
import {ModelSettings} from "./model-settings";
import {GuidedRun, friendly} from "./guided";
import {newIntent, shellStyles, text, action, group, object, strings, gapText, gapHint} from "./shell";
type Detail = "current" | "plan" | "workbench" | "review" | "settings" | "diagnostics" | "setup";

const details: ReadonlyArray<readonly [Detail, string, string]> = [
  ["setup", "开始测试", "新建项目、配置环境与模型、发布计划。"],
  ["plan", "计划", "查看范围、用例树、依据确认和发布状态。"],
  ["workbench", "测试工作台", "查看步骤、当前尝试、证据、人工待办和阻塞原因。"],
  ["review", "问题与报告", "查看问题清单、修复回归、报告修订和本地导出。"],
  ["settings", "设置", "配置模型、环境、凭据引用、存储和显示偏好。"],
  ["diagnostics", "诊断", "连接能力、技术回执与高级操作。"],
];


type Outcome = {
  kind: "outcome";
  action: string | null;
  status: "ok" | "refused" | "unavailable";
  code?: string;
  message?: string;
  response?: Record<string, unknown>;
  clientRequestId?: string;
};

/** 宿主（扩展）声明的当前项目上下文；面板**不猜**项目身份，未声明即未选择。 */
type ProjectContext = {
  kind: "projectContext";
  project_id: string;
  run_id?: string | null;
  scope_name?: string | null;
  context_id?: string;
};

interface HostApi { postMessage(message: unknown): void }

function hostApi(): HostApi | null {
  const acquire = (globalThis as { acquireVsCodeApi?: () => HostApi }).acquireVsCodeApi;
  return typeof acquire === "function" ? acquire() : null;
}

type Row = { record_id: string; revision: number; aggregate_kind: string };

/** 只读动作：核心不弹确认窗口，超时阈值可以短。 */
const READ_ONLY_ACTIONS = new Set([
  "read_view", "query", "events", "doctor", "storage_usage",
  "copy_repair_brief", "check_source", "inspect_case_reuse",
]);

/** 会改变运行状态或判定的动作：完成后要重读视图，否则缺口不会出现在待处理里。 */
const STATE_CHANGING_ACTIONS = new Set([
  "execute_step", "verify_pending", "create_report", "import_external_result",
  "attach_evidence", "pause_run", "resume_run", "cancel_run", "record_local_review",
]);

/** 只读回执里的记录行；面板原样展示，不补造字段、不重算结论。 */
function rowsOf(response: Record<string, unknown> | undefined): Row[] {
  const result = response?.["result"] as Record<string, unknown> | undefined;
  const items = result?.["items"];
  if (!Array.isArray(items)) { return []; }
  const rows: Row[] = [];
  for (const item of items) {
    if (typeof item !== "object" || item === null) { continue; }
    const raw = item as Record<string, unknown>;
    const recordId = raw["record_id"];
    const revision = raw["revision"];
    const kind = raw["aggregate_kind"];
    if (typeof recordId !== "string" || typeof revision !== "number") { continue; }
    rows.push({ record_id: recordId, revision, aggregate_kind: typeof kind === "string" ? kind : "?" });
  }
  return rows;
}

/** 事件行（`aitest.event/2.0`）：只取回执里真实存在的字段，界面不补造。 */
type EventRow = {
  eventSequence: number | null;
  commitSequence: number | null;
  eventType: string;
  recordId: string;
  revision: number | null;
};

/** 从 `events` 回执里解析事件行；字段缺失保留 null，不猜。 */
function eventRowsOf(response: Record<string, unknown> | undefined): EventRow[] {
  const result = response?.["result"] as Record<string, unknown> | undefined;
  const events = result?.["events"];
  if (!Array.isArray(events)) { return []; }
  const rows: EventRow[] = [];
  for (const item of events) {
    if (typeof item !== "object" || item === null) { continue; }
    const raw = item as Record<string, unknown>;
    const sequence = raw["event_sequence"];
    const commit = raw["commit_sequence"];
    const revision = raw["revision"];
    rows.push({
      eventSequence: typeof sequence === "number" ? sequence : null,
      commitSequence: typeof commit === "number" ? commit : null,
      eventType: typeof raw["event_type"] === "string" ? String(raw["event_type"]) : "?",
      recordId: typeof raw["record_id"] === "string" ? String(raw["record_id"]) : "?",
      revision: typeof revision === "number" ? revision : null,
    });
  }
  return rows;
}

/** 事件续读游标（不透明字符串）；只在回执明确给出新游标时前移。 */
function nextCursorOf(response: Record<string, unknown> | undefined): string | null {
  const result = response?.["result"] as Record<string, unknown> | undefined;
  const cursor = result?.["next_cursor"];
  return typeof cursor === "string" && cursor.length > 0 ? cursor : null;
}
const issueFields: ReadonlyArray<readonly [string, string]> = [
  ["description", "说明"],
  ["reproduction", "复现上下文"],
  ["expected", "预期"],
  ["actual", "实际"],
  ["evidence_refs", "证据"],
  ["fact_state", "事实/假设"],
  ["severity", "级别"],
  ["owner_ref", "责任人"],
  ["status", "状态"],
  ["fix_history", "修复/回归"],
];

class AITestPanel extends HTMLElement {
  private readonly guided = new GuidedRun();
  private detail: Detail = "current";
  /** 由宿主注入的编辑器标签页视图：详情页，不渲染侧栏头部。 */
  private standalone = false;
  private doctorRetries = 0;
  private pendingTimer: number | null = null;
  private readonly root = this.attachShadow({ mode: "open" });
  private readonly host = hostApi();
  private readonly modelSettings=new ModelSettings(message=>this.host?.postMessage(message),()=>this.render());
  private readonly onboarding=new Onboarding(message=>this.host?.postMessage(message),command=>{
    this.commandDraft=JSON.stringify(command, (key,value)=>key==="key"?undefined:value);this.submitCommand();
  },()=>this.dispatch("doctor"),()=>this.render(),()=>this.navigate("workbench"));
  private outcome: Outcome | null = null;
  private pending = false;
  private selectedRecord:Row|null=null;
  private recordOutcome:Outcome|null=null;
  private planOutcome:Outcome|null=null;
  /** 用例层级（L1/L2/L3）只存在于已发布的计划记录里；按计划引用读取一次后缓存。 */
  private planLayers:Record<string,string>={};
  /** 每个用例的断言依据状态（missing / present_unconfirmed / confirmed），同样来自计划记录。 */
  private planBasis:Record<string,string>={};
  /** 计划里的 scope.name 就是用户给这次验证起的名字；用它当标题，比"本次测试"可读。 */
  private planScopeName="";
  private planRevision=0;
  /** 计划读取失败时把原因显示出来，避免静默降级成"没有层级"。 */
  private planNote="";
  private planAwait=false;
  private planLookup:string|null=null;
  /** 查询计划记录行时用的修订标识；用它避免对同一计划重复发起查询。 */
  private planQueryRef:string|null=null;
  private expanded:Record<string,boolean>={current:true,records:false};
  private homeScroll=0;
  private selectedStep:string|null=null;
  private selectedStepRun="";
  private intents=new Map<string,string>();
  /** 同一运行复用同一报告标识；预期修订按该标识的当前内容修订发送（#105）。 */
  private reportIds=new Map<string,string>();
  private reportRevisions=new Map<string,number>();
  private reportLookedUp=new Set<string>();
  private reportLookupRun:string|null=null;
  private pendingReportSaveRun:string|null=null;
  private autoReadKey:string|null=null;
  private autoPanelKey:string|null=null;
  private autoReportKey:string|null=null;
  private navigate(detail:Detail):void {
    if(this.detail==="current")this.homeScroll=window.scrollY;
    if(detail==="settings"&&this.host)this.modelSettings.open();
    this.detail=detail;this.render();
    window.scrollTo(0,detail==="current" ? this.homeScroll : 0);
  }
  private view(kind:string):Record<string,unknown>|undefined {
    return object(object(this.viewOutcomes.get(kind)?.response?.["result"])?.["view"]);
  }
  private available(name:string):boolean {
    return !this.pending && this.host!==null && this.project!==null
      && strings(object(this.doctorOutcome?.response?.["result"])?.["supported_actions"]).includes(name);
  }
  private requestOperation(name:string,parameters:Record<string,unknown>,target?:string,expected=0):void {
    const key=JSON.stringify([this.contextId,name,target,expected,parameters]);
    let intent=this.intents.get(key);
    if(!intent){intent=newIntent();this.intents.set(key,intent);}
    this.commandDraft=JSON.stringify({action:name,intent_id:intent,expected_revision:expected,...(target?{target}:{}),parameters});
    this.submitCommand();
  }

  /** 按**运行身份**取报告标识：同一运行的多份报告修订共用一个标识（#105）。 */
  private reportIdFor(runId:string):string {
    const known=this.reportIds.get(runId);
    if(known)return known;
    const created=`report-${newIntent()}`;
    this.reportIds.set(runId,created);
    return created;
  }

  /** 保存报告：身份未知时先按运行读取既有报告，复用标识与当前内容修订。 */
  private saveReport():void {
    const run=this.view("run");
    const runId=typeof run?.["run_id"]==="string" ? run["run_id"] : null;
    if(!runId)return;
    if(!this.available("create_report")){this.commandNote="核心未声明报告保存能力；先刷新核心状态或完成所需材料。";this.render();return;}
    const knownId=this.reportIds.get(runId);
    if(knownId!==undefined&&this.reportRevisions.has(knownId)){
      this.requestOperation("create_report",{run_id:runId,report_id:knownId,issue_ids:[]},undefined,this.reportRevisions.get(knownId)!);
      return;
    }
    if(!this.reportLookedUp.has(runId)){
      // 面板重启后本地没有身份缓存：先只读该运行的报告，按回执确定标识与修订，
      // 不猜报告编号，也不把新旧报告混成一条。
      this.reportLookupRun=runId;
      this.pendingQuery="reports";
      this.dispatchScoped("query",{run_id:runId,limit:5});
      return;
    }
    // 该运行确实还没有报告（回执为空）：首次保存使用新的业务身份。
    const reportId=knownId ?? this.reportIdFor(runId);
    this.reportRevisions.set(reportId,0);
    this.requestOperation("create_report",{run_id:runId,report_id:reportId,issue_ids:[]},undefined,0);
  }

  /** 从真实回执吸收报告身份：同一运行保留一个标识，修订取回执行给出的值。 */
  private absorbReportRows(data:Outcome):void {
    if(data.status!=="ok")return;
    const runId=this.reportLookupRun ?? this.project?.run_id ?? null;
    let latest:Row|null=null;
    for(const row of rowsOf(data.response)){
      if(row.aggregate_kind!=="report")continue;
      const known=this.reportRevisions.get(row.record_id) ?? 0;
      if(row.revision>known)this.reportRevisions.set(row.record_id,row.revision);
      if(latest===null||row.revision>latest.revision)latest=row;
    }
    // 同一运行历史遗留的多条报告只取修订最高的一条作为后续修订的载体；
    // 其余行保持可读，不被删除或合并。
    if(runId!==null&&latest!==null)this.reportIds.set(runId,latest.record_id);
  }

  private project: ProjectContext | null = null;
  private contextId: string | null = null;
  private issueOutcome: Outcome | null = null;
  private reportOutcome: Outcome | null = null;
  /** 当前打开的问题详情身份（来自清单回执行，不猜）。 */
  private issueDetail: Row | null = null;
  private issueDetailOutcome: Outcome | null = null;
  private copyNote: string | null = null;
  /** 事件续读：游标只在回执明确给出新游标时前移，拒绝时保持原游标。 */
  private eventCursor: string | null = null;
  private eventOutcome: Outcome | null = null;
  private eventLog: EventRow[] = [];
  private eventNote: string | null = null;
  /** 复测简报（`copy_repair_brief`，只读）：按当前问题身份读取，界面原样展示。 */
  private briefOutcome: Outcome | null = null;
  private briefIssueId: string | null = null;
  private briefNote: string | null = null;
  /** 最近一次只读查询的作用域：回执只有动作名，靠它把结果归位（不猜）。 */
  private pendingQuery: "issues" | "reports" | "issue-detail" | "plans" | null = null;
  private pendingRequest: { id: string; action: string } | null = null;
  private requestSequence = 0;
  private readonly requestPrefix = `panel-${Date.now().toString(36)}`;
  private doctorOutcome: Outcome | null = null;
  private commandDraft = "";
  private commandNote = "";
  private projectName = "";
  private projectGoal = "";
  private workflowAction = "publish_plan";
  private workflowValues: Record<string,string> = {expected_revision:"0"};
  private viewOutcomes = new Map<string, Outcome>();
  private deliveryLookup = {delivery_id:"", submission_id:""};

  private readonly receiveMessage = (event: MessageEvent): void => {
      if(this.modelSettings.receive(event.data as Record<string,unknown>))return;
      if(this.onboarding.receive(event.data as Record<string,unknown>))return;
      const data = event.data as Outcome | ProjectContext | undefined;
      if (!data || typeof data !== "object") { return; }
      if (data.kind === "projectContext") {
        if (typeof data.project_id !== "string" || data.project_id.length > 128
            || /[\u0000-\u001f]/.test(data.project_id)
            || (data.run_id != null && (typeof data.run_id !== "string" || data.run_id.length > 128))) { return; }
        const next = data.project_id.trim()
          ? { kind: "projectContext", project_id: data.project_id, run_id: data.run_id ?? null,
              scope_name: typeof data.scope_name === "string" ? data.scope_name : null,
              context_id: data.context_id, } as ProjectContext
          : null;
        if (JSON.stringify(next) !== JSON.stringify(this.project)) {
          if (this.contextId && data.context_id !== this.contextId) {
            this.doctorOutcome = null;
          }
          this.issueOutcome = this.reportOutcome = this.issueDetailOutcome = null;
          this.selectedRecord=null;this.recordOutcome=this.planOutcome=null;
          this.planLayers={};this.planBasis={};this.planScopeName="";this.planRevision=0;this.planNote="";this.planAwait=false;this.planLookup=null;this.planQueryRef=null;
          this.eventOutcome = this.briefOutcome = null;
          this.eventLog = []; this.eventCursor = this.eventNote = null;
          this.briefIssueId = this.briefNote = null;
          this.guided.reset();
          this.viewOutcomes.clear(); this.selectedStep=null; this.intents.clear(); this.pendingQuery = null; this.outcome = null;
          this.reportIds.clear(); this.reportRevisions.clear(); this.reportLookedUp.clear();
          this.reportLookupRun = null; this.pendingReportSaveRun = null; this.autoReadKey = this.autoPanelKey = this.autoReportKey = null;
          this.commandDraft = ""; this.commandNote = this.pendingRequest ? "项目已切换，请核对当前步骤后继续。" : "";
          this.projectName = this.projectGoal = "";
          this.workflowValues = {expected_revision:"0"};
          this.deliveryLookup = {delivery_id:"", submission_id:""};
          if (this.pendingRequest && !["doctor", "ensure-core"].includes(this.pendingRequest.action)) {
            this.pendingRequest = null; this.pending = false;
          }
        }
        const contextChanged = this.contextId !== null && data.context_id !== this.contextId;
        this.contextId = typeof data.context_id === "string" ? data.context_id : null;
        this.project = next;
        this.render();
        if (contextChanged && !this.pending && !this.onboarding.isConfiguring()) { this.dispatch("doctor"); }
        this.maybeAutoRead();
        return;
      }
      if (data.kind !== "outcome") { return; }
      if (!["ok", "refused", "unavailable"].includes(data.status)) { return; }
      const correlation = data.clientRequestId ?? data.response?.["request_id"];
      if (this.pendingRequest) {
        if (correlation !== this.pendingRequest.id || data.action !== this.pendingRequest.action) { return; }
      } else if (this.host !== null || data.action !== "doctor") { return; }
      this.outcome = data;
      if(data.action!=="read_view")this.guided.receive(data.action ?? "",data.status,object(data.response?.["result"]));
      if (data.action === "doctor") {
        this.doctorOutcome = data;
        // 两个面板（侧栏 + 编辑器页）可能同时连接核心，抢输的一方等一会儿再试。
        if (data.status !== "ok" && this.host !== null && this.doctorRetries < 3) {
          this.doctorRetries += 1;
          window.setTimeout(() => { if (this.host !== null) { this.dispatch("doctor"); } }, 1200 * this.doctorRetries);
        }
      }
      if (data.action === "query") {
        if (this.pendingQuery === "plans") { this.planOutcome=data; }
        // 按**本次查询的作用域**归位；没有待归位作用域时只更新最近回执。
        if (this.pendingQuery === "issues") { this.issueOutcome = data; }
        if (this.pendingQuery === "reports") {
          this.reportOutcome = data;
          this.absorbReportRows(data);
          // 保存前先读取既有报告：成功后在同一运行上继续保存下一修订，
          // 失败时保留真实原因，不凭猜的修订号提交（#105）。
          if (this.reportLookupRun !== null) {
            if (data.status === "ok") {
              this.reportLookedUp.add(this.reportLookupRun);
              this.pendingReportSaveRun = this.reportLookupRun;
            }
            else { this.commandNote = `读取既有报告未完成：${data.code ?? data.status}：${data.message ?? ""}`; }
            this.reportLookupRun = null;
          }
        }
        if (this.pendingQuery === "issue-detail") { this.issueDetailOutcome = data; }
        this.pendingQuery = null;
      }
      if (data.action === "events") {
        this.applyEventOutcome(data);
      }
      if (data.action === "copy_repair_brief") {
        this.briefOutcome = data;
        this.briefNote = data.status === "ok" ? null : "核心未通过：简报未取得。";
      }
      if (data.action === "read_view") {
        const result = data.response?.["result"] as Record<string, unknown> | undefined;
        const view = result?.["view"] as Record<string, unknown> | undefined;
        const kind = result?.["view_type"];
        if(this.selectedRecord && view && this.project && data.status==="ok" && view["project_id"]===this.project.project_id
          && kind===this.selectedRecord.aggregate_kind && view["record_id"]===this.selectedRecord.record_id
          && view["record_revision"]===this.selectedRecord.revision
          && Number.isSafeInteger(result?.["commit_id"]) && typeof result?.["event_cursor"]==="string") {
          this.recordOutcome=data;
        }
        if (data.status === "ok" && this.project && typeof kind === "string"
            && ["run", "panel", "delivery"].includes(kind)
            && typeof view === "object" && view !== null && !Array.isArray(view)
            && view["project_id"] === this.project.project_id
            && (kind === "delivery" || view["run_id"] === this.project.run_id)
            && Number.isSafeInteger(result?.["commit_id"])
            && Number(result?.["commit_id"]) >= 0
            && typeof result?.["event_cursor"] === "string" && result["event_cursor"].length > 0) {
          this.viewOutcomes.set(kind, data);
          this.guided.receive("read_view",data.status,object(data.response?.["result"]));
          // 换运行后旧的步骤选择不再有效，清掉避免工作台指向已不存在的步骤。
          if(kind==="run"&&typeof view["run_id"]==="string"){
            const runRef=String(view["run_id"]);
            if(runRef!==this.selectedStepRun){this.selectedStepRun=runRef;this.selectedStep=null;}
          }
        }
        if(data.status==="ok" && kind==="plan" && view && this.project && view["project_id"]===this.project.project_id){
          // 记录视图把载荷包在 record 下；层级与范围名都在里面。
          const payload=object(view["record"]) ?? view;
          const cases=Array.isArray(payload["cases"])?payload["cases"]:[];
          const layers:Record<string,string>={};
          const basis:Record<string,string>={};
          for(const raw of cases){
            const item=object(raw);
            if(!item || typeof item["case_id"]!=="string")continue;
            if(typeof item["layer"]==="string")layers[item["case_id"]]=item["layer"];
            if(typeof item["assertion_basis_state"]==="string")basis[item["case_id"]]=item["assertion_basis_state"];
          }
          if(Object.keys(layers).length){this.planLayers=layers;}
          if(Object.keys(basis).length){this.planBasis=basis;}
          const scope=object(payload["scope"]);
          if(typeof scope?.["name"]==="string" && scope["name"].trim())this.planScopeName=String(scope["name"]);
          if(Number.isSafeInteger(payload["revision"]))this.planRevision=Number(payload["revision"]);
          else if(Number.isSafeInteger(view["record_revision"]))this.planRevision=Number(view["record_revision"]);
          this.planNote="";
        }
        if(this.planAwait && data.action==="read_view"){
          this.planAwait=false;
          if(!(data.status==="ok" && kind==="plan"))
            this.planNote=`计划读取未完成：${data.code ?? data.status}${data.message?`：${data.message}`:""}`;
        }
      }
      if(data.action && !["doctor","query","events","read_view","ensure-core","copy_repair_brief"].includes(data.action)){
        const saved=object(data.response?.["result"]);
        this.commandNote=data.status!=="ok" ? `操作未完成：${data.message ?? "请刷新状态后重试同一操作，或打开诊断查看原因。"}`
          : saved?.["blocked"]===true ? "操作受阻，请检查所需材料。"
          : data.action==="create_report" ? "报告已保存。点击“读取报告修订”查看。"
          : data.action==="export_report" ? exportNote(saved) : "本次请求已返回，请查看步骤结果。";
      }
      if (data.action === "create_report" && data.status === "ok") {
        const saved=object(data.response?.["result"]);
        const recordId=typeof saved?.["record_id"]==="string" ? saved["record_id"] : null;
        const revision=saved?.["revision"];
        if(recordId!==null&&Number.isSafeInteger(revision)&&Number(revision)>0){
          this.reportRevisions.set(recordId,Number(revision));
          if(this.project?.run_id)this.reportIds.set(this.project.run_id,recordId);
        }
      }
      this.pendingRequest = null;
      this.pending = false;
      this.disarmWatchdog();
      this.render();
      this.pumpPlanLookup();
      // 能力协商（doctor）通常在首屏之后才回来；回来后要再试一次自动读取，否则一直停在空状态。
      this.maybeAutoRead();
      // 执行/核验/存报告会改变判定与缺口：立刻重读运行与面板视图，让"待处理"反映最新缺口。
      if (STATE_CHANGING_ACTIONS.has(String(data.action ?? ""))) { this.refreshViews(); }
      this.onboarding.outcome(data.action??"",data.status,object(data.response?.["result"]),data.code,data.message);
      if (data.action === "ensure-core" && data.status === "ok") {
        this.dispatch("doctor");
      }
      if (this.pendingReportSaveRun !== null) {
        this.pendingReportSaveRun = null;
        this.saveReport();
      }
      this.maybeAutoRead();
  };

  /** 重启或切换运行后自动读取一次最近运行摘要，不再要求先手动刷新（#108）。 */
  private maybeAutoRead():void {
    const runId=this.project?.run_id;
    if(!runId||this.host===null||this.pending||this.onboarding.isConfiguring())return;
    const key=`${this.contextId ?? ""}:${runId}`;
    if(this.autoReadKey===key||this.viewOutcomes.has("run")||!this.available("read_view")){
      // 运行视图到手后补读一次面板视图：缺口分类与业务结论都只在面板视图里。
      if(this.viewOutcomes.has("run") && !this.viewOutcomes.has("panel")
          && this.autoPanelKey!==key && this.available("read_view")){
        this.autoPanelKey=key;
        this.dispatchScoped("read_view",{view:"panel",run_id:runId});
        return;
      }
      // 再读一次报告修订列表，「测试记录」里的最近报告才有内容；失败时原样显示回执。
      if(this.viewOutcomes.has("run") && this.reportOutcome===null
          && this.autoReportKey!==key && this.available("query")){
        this.autoReportKey=key;
        this.pendingQuery="reports";
        this.dispatchScoped("query",{run_id:runId,limit:5});
      }
      return;
    }
    this.autoReadKey=key;
    this.dispatchScoped("read_view",{view:"run",run_id:runId});
  }

  connectedCallback(): void {
    // 宿主在编辑器页面里会写入初始视图标记，让它直接落在工作台或报告页。
    const preset=(window as unknown as {__AITEST_DETAIL?:unknown}).__AITEST_DETAIL;
    if(preset==="workbench"||preset==="review"){
      this.detail=preset;
      this.standalone=true;
    }
    window.addEventListener("message", this.receiveMessage);
    this.render();
    this.dispatch("doctor");
  }

  disconnectedCallback(): void {
    window.removeEventListener("message", this.receiveMessage);
    this.pendingRequest = null; this.pendingQuery = null; this.pending = false;
  }

  /**
   * 事件续读的归位（设计 §12「已提交事件增量刷新」）：
   * - 成功：把回执行**追加**到事件流（去重按事件序号），并把游标前移到回执给出的 `next_cursor`；
   * - 拒绝（游标非法/需维护）：原样显示状态与代码，**保持原游标与既有事件行**，绝不静默重置；
   * - 界面不重算业务顺序：业务顺序看 `commit_sequence`，只如实展示。
   */
  private applyEventOutcome(data: Outcome): void {
    this.eventOutcome = data;
    const resultStatus = (data.response?.["result"] as Record<string, unknown> | undefined)
      ?.["status"];
    // 真实 `events` 动作把 `invalid_cursor`/`maintenance_required` 放在 result.status 里
    // （宿主层 status 仍是 ok）：两者都必须原样显示，并保持游标与既有事件行。
    if (data.status !== "ok" || (typeof resultStatus === "string" && resultStatus !== "ok")) {
      const code = data.code ?? (typeof resultStatus === "string" ? resultStatus : data.status);
      this.eventNote = `核心未通过 · ${code}：${data.message ?? ""}`
        + "（游标与既有事件行保持不变）";
      return;
    }
    const rows = eventRowsOf(data.response);
    for (const row of rows) {
      const duplicate = row.eventSequence !== null
        && this.eventLog.some(seen => seen.eventSequence === row.eventSequence);
      if (!duplicate) { this.eventLog.push(row); }
    }
    const cursor = nextCursorOf(data.response);
    if (rows.length === 0) {
      this.eventNote = "没有新事件（回执为空；已到已知边界）。";
    } else {
      this.eventNote = `已追加 ${rows.length} 条事件（去重按事件序号）。`;
    }
    if (cursor !== null) {
      this.eventCursor = cursor;
    } else if (rows.length > 0) {
      this.eventNote += " 回执未给出新游标：游标保持不变。";
    }
  }

  /** 面板只发起**只读**查询；写动作由受控用户入口发起，界面点击不等于确认。 */
  /** 状态变化后重读运行视图；面板视图会随后自动补读，于是缺口立刻可见。 */
  private refreshViews(): void {
    if (this.host === null || this.pending) { return; }
    if (!this.available("read_view") || !this.project?.run_id) { return; }
    this.autoPanelKey = null;
    this.dispatchScoped("read_view", { view: "run", run_id: this.project.run_id });
  }

  /** 回执看门狗：读操作短、需要原生确认窗口的写操作长（窗口本身最长 2 分钟）。 */
  private armWatchdog(timeoutMs: number): void {
    this.disarmWatchdog();
    this.pendingTimer = window.setTimeout(() => {
      this.pendingTimer = null;
      if (!this.pending) { return; }
      this.pending = false; this.pendingRequest = null; this.pendingQuery = null;
      this.commandNote = "请求未在限定时间内返回。可以重试同一操作；核心结果未知时不会自动重跑。";
      this.render();
    }, timeoutMs);
  }

  private disarmWatchdog(): void {
    if (this.pendingTimer !== null) { window.clearTimeout(this.pendingTimer); this.pendingTimer = null; }
  }

  private dispatch(action: string, parameters?: Record<string, unknown>): void {
    if (this.host === null) {
      this.outcome = {
        kind: "outcome", action, status: "unavailable",
        message: "当前不在编辑器宿主中，面板未连接核心",
      };
      this.render();
      return;
    }
    this.pending = true;
    // 只读动作应当很快返回；写动作可能等用户在原生确认窗口里点确认。
    this.armWatchdog(READ_ONLY_ACTIONS.has(action) ? 30_000 : 180_000);
    const clientRequestId = `${this.requestPrefix}-${++this.requestSequence}`;
    this.pendingRequest = { id: clientRequestId, action };
    const message: Record<string, unknown> = {
      kind: "dispatch", action, parameters: parameters ?? {}, clientRequestId,
    };
    if (this.project !== null) { message["projectId"] = this.project.project_id; }
    this.host.postMessage(message);
    this.render();
  }

  /** 项目作用域的只读查询：没有宿主项目上下文时不派发（不猜项目身份）。 */
  private dispatchScoped(action: string, parameters?: Record<string, unknown>): void {
    if (this.project === null) {
      this.outcome = {
        kind: "outcome", action, status: "refused", code: "PROJECT_NOT_SELECTED",
        message: "宿主尚未声明当前项目；面板不猜项目身份，请先在编辑器选择项目",
      };
      this.render();
      return;
    }
    this.dispatch(action, parameters);
  }

  private statusText(): string {
    if (this.pending) { return "已请求核心，等待回执…"; }
    const outcome = this.doctorOutcome ?? this.outcome;
    if (outcome === null) { return "尚未连接核心"; }
    if (outcome.status === "ok") {
      const result = outcome.response?.["result"] as Record<string, unknown> | undefined;
      const status = typeof result?.["status"] === "string" ? result["status"] : "OK";
      const actions = Array.isArray(result?.["supported_actions"])
        ? (result["supported_actions"] as unknown[]).length : 0;
      return `核心回执 · ${outcome.action} · ${status} · 可派发动作 ${actions}`;
    }
    const code = outcome.code ?? outcome.status;
    return `核心未通过 · ${outcome.action ?? "-"} · ${code}：${outcome.message ?? ""}`;
  }

  private render(): void {
    this.root.replaceChildren();
    const style = document.createElement("style");
    style.textContent = shellStyles;
    const main = document.createElement("main");
    // 编辑器标签页是详情页：不渲染侧栏头部，避免重复出现项目选择与模型状态。
    if(!this.standalone)main.append(this.header());
    main.append(this.detail === "current" ? this.currentView() : this.detailView());
    if(this.detail!=="current")main.append(this.connectionFooter());
    this.root.append(style, main);
  }

  private header(): HTMLElement {
    const header=document.createElement("header");
    const bar=document.createElement("div");bar.className="toolbar";
    bar.append(text("h1","AI 测试"),action("设置",()=>this.navigate("settings")),action("更多",()=>this.navigate("diagnostics")));
    const project=document.createElement("details");project.className="project-picker";
    project.append(text("summary",this.project ? this.project.scope_name || "当前测试项目" : "选择测试项目"));
    project.append(text("p",this.project ? `项目编号：${this.project.project_id}` : "从本地目录开始，按向导准备测试。","muted subtle"),
      action("新建测试",()=>this.navigate("setup")),action("管理项目连接",()=>this.host?.postMessage({kind:"openSettings"}),!this.host));
    project.dataset.testid="project-context";
    const model=object(object(this.doctorOutcome?.response?.["result"])?.["model"]);
    const modelStatus=document.createElement("div");modelStatus.className="model-line";
    modelStatus.append(text("span",`插件模型 API · ${model?.["status"] ?? this.modelSettings.status()}`,"muted"),action("配置",()=>this.navigate("settings")));
    header.append(bar,project,modelStatus);return header;
  }

  private ensureCore(): void {
    if (this.host === null) {
      return;
    }
    this.pending = true;
    const clientRequestId = `${this.requestPrefix}-${++this.requestSequence}`;
    this.pendingRequest = { id: clientRequestId, action: "ensure-core" };
    this.armWatchdog(90_000);
    this.host.postMessage({ kind: "ensureCore", clientRequestId });
    this.render();
  }

  private currentView(): HTMLElement {
    const fragment=document.createElement("div");fragment.className="dashboard";
    const fold=(label:string,key:string,...children:HTMLElement[])=>group(label,key,this.expanded[key]??(key==="pending"?!!this.project?.run_id:key==="plan"?!!this.project:false),open=>{this.expanded[key]=open;},...children);
    const links=document.createElement("div");links.className="link-row";
    if(this.project)links.append(action("查看准备详情",()=>this.navigate("plan")),action("问题与报告",()=>this.navigate("review")));
    const plan=document.createElement("div");
    const openPlan=action("打开计划与用例",()=>this.navigate("plan"));openPlan.className="link-button";
    plan.append(openPlan,this.caseTree());
    const records=document.createElement("div");
    // 自动读取之外的显式入口：即使自动那次失败或被能力门禁挡住，也能手动取一次。
    const loadReports=action("读取最近报告",()=>{
      if(!this.project?.run_id)return;
      this.pendingQuery="reports";
      this.dispatchScoped("query",{run_id:this.project.run_id,limit:5});
    },!this.available("query")||!this.project?.run_id);
    records.append(loadReports,action("完整报告列表",()=>this.navigate("review")),this.rowsBlock("最近报告",this.reportOutcome,"recent-reports"));
    fragment.append(fold("当前测试","current",this.phaseSection(),this.actionSection(),links),
      fold("待处理","pending",this.pendingSection()),fold("测试计划","plan",plan),fold("测试记录","records",records));
    fragment.append(this.connectionFooter());return fragment;
  }

  private connectionFooter():HTMLElement {
    const footer=text("footer",this.pending ? "正在处理，请稍候…" : this.doctorOutcome?.status==="ok" ? "核心已连接 · 本地工作空间" : "尚未连接核心 · 可在设置中连接","muted");
    footer.dataset.testid="panel-status";footer.setAttribute("role","status");return footer;
  }

  private pendingSection():HTMLElement {
    const section=document.createElement("div");const view=this.view("run") ?? this.view("panel");
    if(!view){section.append(text("p",this.project?.run_id ? "读取本次运行后，显示需要你处理的事项。" : "先完成测试准备，再核对操作授权。","muted"));}
    else {
      const steps=Array.isArray(view.steps)
        ? view.steps.map(object).filter((item):item is Record<string,unknown>=>item!==undefined)
        : [];
      const groups:[string,string][]=[
        ["manual_pending_step_ids","待人工"],
        ["pending_verification_step_ids","待核验"],
        ["blocked_step_ids","阻塞"],
        ["invalidated_step_ids","依据过期"],
      ];
      const gaps=strings(view.gap_codes);
      // 缺口分两类，和设计里的"缺证据 / 真实性未知"对应：结果未知或身份未核实的算后者。
      const unknownGaps=gaps.filter(code=>/unknown|unverified/.test(String(code)));
      const missingGaps=gaps.filter(code=>!unknownGaps.includes(code));
      const unfinished=steps.filter(step=>step["state"]==="pending");
      // 先给一行总量，再看明细：侧栏一屏放不下逐条清单。
      const counts=groups.map(([key,label])=>[label,strings(view[key]??[]).length] as const).filter(([,n])=>n>0);
      if(unfinished.length)counts.push(["待执行",unfinished.length] as const);
      if(missingGaps.length)counts.push(["缺证据",missingGaps.length] as const);
      if(unknownGaps.length)counts.push(["真实性未知",unknownGaps.length] as const);
      if(counts.length){
        const countLine=text("p",counts.map(([label,n])=>`${label} ${n}`).join(" · "),"muted");
        countLine.dataset.testid="pending-counts";
        section.append(countLine);
      }
      const groupHints:Record<string,string>={
        manual_pending_step_ids:"需要你在本机完成操作，然后记录实际结果。",
        pending_verification_step_ids:"打开该步骤，按独立预期核验这次执行结果。",
        blocked_step_ids:"先处理阻塞原因，再继续执行。",
        invalidated_step_ids:"上游结果已变化，需要重新执行该步骤。",
      };
      for(const [key,label] of groups){
        for(const id of strings(view[key]??[])){
          const index=steps.findIndex(step=>step["step_id"]===id);
          const name=caseKindName(steps[index]?.["case_id"]);
          const item=action(`${label} · ${name||(index>=0?`第 ${index+1} 步`:"打开对应步骤")}`,()=>{this.selectedStep=id;this.navigate("workbench");});
          item.className="link-button";
          const row=document.createElement("div");row.className="pending-row";
          row.append(item,text("span",label,"tag"));
          section.append(row);
          const hint=groupHints[key];
          if(hint)section.append(text("p",hint,"hint"));
        }
      }
      if(gaps.length){
        const list=document.createElement("div");list.className="pending-list";
        // 报告判定说"有用例未取得有效验证"时，即使 step 状态已完成，也必须提示回去核验。
        const verifyGap=gaps.some(code=>/required_case_unverified|required_case_pending_verification/.test(String(code)));
        if(verifyGap){
          const item=action("待核验 · 有已执行用例未取得有效验证",()=>{this.selectedStep=this.attention()?.stepId ?? null;this.navigate("workbench");});
          item.className="link-button";
          const row=document.createElement("div");row.className="pending-row";
          row.append(item,text("span","待核验","tag"));
          section.append(row);
        }
        for(const code of gaps){
          list.append(text("p",`证据缺口：${gapText(code,"")}`,"muted"));
          const hint=gapHint(code);
          if(hint)list.append(text("p",hint,"hint"));
        }
        section.append(list);
      }
      // 还没跑过的步骤也要出现在待处理里，否则用户以为"没事可做"。
      if(unfinished.length){
        section.append(text("p",`待执行：${unfinished.length} 个步骤`,"muted"));
        // 竖排：横向排列会在窄侧栏里把两条挤在同一行并断字。
        const list=document.createElement("div");list.className="pending-list";
        for(const step of unfinished.slice(0,10)){
          const stepId=String(step["step_id"]??"");
          const item=action(`待执行 · ${caseKindName(step["case_id"])||"步骤"}`,()=>{this.selectedStep=stepId;this.navigate("workbench");});
          item.className="link-button";list.append(item);
        }
        section.append(list);
      }
      if(!section.childElementCount)section.append(text("p","本次视图未列出待处理项；其他未查询事实保持待核实。","muted"));
    }
    if(this.project?.run_id)section.append(action("读取运行详情",()=>this.dispatchScoped("read_view",{view:"run",run_id:this.project?.run_id}),!this.available("read_view")));return section;
  }

  private caseTree():HTMLElement {
    const wrap=document.createElement("div");
    // 计划名与修订来自计划记录；没有就只显示用例列表，不编造计划信息。
    if(this.planScopeName||this.planRevision){
      const total=Object.keys(this.planLayers).length;
      const planHead=text("p",`${this.planScopeName||"本次计划"}${this.planRevision?` · 修订 ${this.planRevision}`:""}${total?` · ${total} 项`:""}`,"muted");
      planHead.dataset.testid="plan-heading";
      wrap.append(planHead);
    }
    if(this.planNote)wrap.append(text("p",this.planNote,"muted"));
    const list=document.createElement("ul");list.className="case-tree";list.setAttribute("aria-label","计划用例");
    const view=this.view("run");const steps=Array.isArray(view?.["steps"]) ? view["steps"] : [];
    const cases=strings(view?.["selected_case_ids"]);
    if(!view){list.append(text("li",this.project?.run_id ? "读取运行后显示本次计划与用例。" : "发布计划后，用例会显示在这里。","empty"));wrap.append(list);return wrap;}
    for(const [caseIndex,id] of cases.entries()){
      const item=document.createElement("li");const children=document.createElement("ul");
      // 用例编号后不带名称时，用户无法分辨四项检查；按用例编号后缀映射到检查名称。
      const caseName=caseKindName(id);
      // 读到计划记录后显示 L1/L2/L3；读不到时退回编号 + 检查名称，不猜层级。
      const layer=this.planLayers[id];
      const head=layer ? `${layer} · ${caseName||`用例 ${caseIndex+1}`}` : `用例 ${caseIndex+1}${caseName?` · ${caseName}`:""}`;
      item.append(text("strong",`${head}${strings(view["required_case_ids"]).includes(id)?" · 必测":""}`));
      for(const raw of steps){const step=object(raw);if(step?.["case_id"]!==id)continue;
        const child=document.createElement("li");child.append(action(`步骤 ${children.childElementCount+1} · ${friendly(step["state"])}`,()=>{this.selectedStep=String(step["step_id"]);this.navigate("workbench");}));children.append(child);}
      item.append(children);list.append(item);
    }
    wrap.append(list);
    return wrap;
  }

  private controlledSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "controlled-entry";
    const title = document.createElement("h2"); title.textContent = "受控操作";
    const note = document.createElement("p"); note.className = "muted";
    const capabilities = this.doctorOutcome?.response?.["result"] as Record<string, unknown> | undefined;
    const nativeAvailable = Array.isArray(capabilities?.["transport_capabilities"])
      && capabilities["transport_capabilities"].includes("native-confirmation/1");
    note.textContent = "先配置项目编号。此处请求核心窗口确认完整输入；窗口确认前不会提交。"
      + "计划、环境和执行仍须满足核心门禁。未知结果保留原意图查询，不自动重跑。";
    const create = document.createElement("button"); create.textContent = "填写新建项目";
    const name = document.createElement("input");
    name.setAttribute("aria-label", "项目名称"); name.placeholder = "项目名称";
    name.value = this.projectName; name.disabled = this.pending;
    name.oninput = () => { this.projectName = name.value; };
    const goal = document.createElement("textarea");
    goal.setAttribute("aria-label", "实际测试目标"); goal.placeholder = "填写实际测试目标";
    goal.rows = 3; goal.value = this.projectGoal; goal.disabled = this.pending;
    goal.oninput = () => { this.projectGoal = goal.value; };
    create.disabled = this.pending || this.project === null || this.host === null || !nativeAvailable;
    create.onclick = () => {
      const workspaceId = this.doctorOutcome?.response?.["workspace_id"];
      if (typeof workspaceId !== "string" || this.project === null) {
        this.commandNote = "请先刷新核心状态，取得已核实的工作空间身份。";
      } else if (!this.projectName.trim() || !this.projectGoal.trim()) {
        this.commandNote = "请填写项目名称和实际测试目标。";
      } else {
        const intentId = Array.from(crypto.getRandomValues(new Uint8Array(16)),
          value => value.toString(16).padStart(2, "0")).join("");
        this.commandDraft = JSON.stringify({action:"save_context", intent_id:intentId,
          expected_revision:0, parameters:{project:{schema_version:"aitest.project/2.0",
            local_project_id:this.project.project_id, project_id:this.project.project_id,
            workspace_id:workspaceId, name:this.projectName.trim(), goal:this.projectGoal.trim(),
            revision:1, created_at_commit:"@core-assigned", modules:[]}}}, null, 2);
        this.commandNote = "项目输入已准备，请求核心确认后保存。已有同编号项目会拒绝覆盖。";
        this.outcome = null;
      }
      this.render();
    };
    const draft = document.createElement("textarea");
    draft.setAttribute("aria-label", "受控操作 JSON"); draft.rows = 14;
    draft.placeholder = '{"action":"…","intent_id":"稳定业务意图","expected_revision":0,"parameters":{}}';
    draft.value = this.commandDraft; draft.disabled = this.pending;
    draft.oninput = () => { this.commandDraft = draft.value; };
    const submit = document.createElement("button"); submit.textContent = "请求核心确认并提交";
    submit.disabled = this.pending || this.project === null || this.host === null || !nativeAvailable;
    if (!nativeAvailable) { note.textContent += " 当前核心尚未声明原生确认能力，请连接并刷新新版核心。"; }
    submit.onclick = () => this.submitCommand();
    const status = document.createElement("p"); status.textContent = this.commandNote;
    const receipt = document.createElement("pre");
    receipt.style.whiteSpace = "pre-wrap"; receipt.style.overflowWrap = "anywhere";
    receipt.textContent = this.outcome?.response ? JSON.stringify(this.outcome.response, null, 2) : "";
    if (this.outcome && this.outcome.action !== "doctor") {
      status.textContent = this.outcome.status === "ok"
        ? "核心已返回本次操作结果；请以回执中的具体业务状态为准。"
        : `${this.outcome.code ?? this.outcome.status}：${this.outcome.message ?? "操作结果尚未核实"}`;
    }
    section.append(title, note, name, goal, create, this.workflowSection(), draft, submit, status, receipt);
    return section;
  }

  private submitCommand():void {

      try {
        const raw: unknown = JSON.parse(this.commandDraft);
        if (!raw || typeof raw !== "object" || Array.isArray(raw)) { throw new Error("输入须为 JSON 对象"); }
        const command = raw as Record<string, unknown>;
        const allowed = ["action", "intent_id", "expected_revision", "binding_revision", "target", "parameters"];
        if (Object.keys(command).some(key => !allowed.includes(key))
            || typeof command["action"] !== "string" || typeof command["intent_id"] !== "string"
            || !Number.isSafeInteger(command["expected_revision"]) || Number(command["expected_revision"]) < 0
            || !command["parameters"] || typeof command["parameters"] !== "object"
            || Array.isArray(command["parameters"])) { throw new Error("请核对动作、意图、预期修订和参数"); }
        const clientRequestId = `${this.requestPrefix}-${++this.requestSequence}`;
        this.pendingRequest = {id:clientRequestId, action:command["action"]};
        this.pending = true;
        this.armWatchdog(180_000);
        this.commandNote = "已请求核心窗口；请核对窗口里的准确输入和保存依据。";
        this.host?.postMessage({kind:"controlledDispatch", clientRequestId, contextId:this.contextId,
          action:command["action"], intentId:command["intent_id"],
          expectedRevision:command["expected_revision"], bindingRevision:command["binding_revision"],
          target:command["target"], parameters:command["parameters"], projectId:this.project?.project_id});
      } catch (error) {
        this.commandNote = error instanceof Error ? error.message : "操作输入无效";
      }
      this.render();
  }

  private workflowSection(): HTMLElement {
    const section=document.createElement("section"); section.dataset.testid="workflow";
    const title=document.createElement("h3"); title.textContent="计划 → 执行 → 核验 → 报告";
    const note=document.createElement("p");
    note.textContent="逐项准备输入，再请求核心确认。解析、授权、执行同一步骤必须沿用同一业务意图；"
      + "其他动作使用各自意图。回执未知时保留原意图，不把新意图当重试。计划/准备仍需准确已保存材料。";
    const choice=document.createElement("select"); choice.setAttribute("aria-label","流程动作");
    choice.disabled=this.pending;
    for (const item of workflowActions) {
      const option=document.createElement("option"); option.value=item.action; option.textContent=item.title;
      choice.append(option);
    }
    choice.value=this.workflowAction;
    choice.onchange=()=>{
      const execution=["prepare_execution","authorize_step","execute_step"];
      if (!(execution.includes(this.workflowAction) && execution.includes(choice.value))) {
        this.workflowValues["intent_id"]="";
      }
      this.workflowValues["expected_revision"]="0";
      this.workflowAction=choice.value; this.render();
    };
    section.append(title,note,choice);
    const fields=[{key:"intent_id",label:"本动作稳定业务意图"},
      {key:"expected_revision",label:"预期仓库修订（首写为 0）"},
      ...workflowActions.find(item=>item.action===this.workflowAction)!.fields];
    for (const field of fields) {
      const label=document.createElement("label"); label.textContent=field.label;
      const input=document.createElement("textarea"); input.rows=("kind" in field && field.kind==="json") ? 5 : 1;
      input.setAttribute("aria-label",field.label); input.value=this.workflowValues[field.key] ?? "";
      input.disabled=this.pending; input.oninput=()=>{this.workflowValues[field.key]=input.value;};
      label.append(input); section.append(label);
    }
    const fill=document.createElement("button"); fill.textContent="准备本步骤操作";
    fill.disabled=this.pending || !this.project || !this.host;
    fill.onclick=()=>{
      try {
        this.commandDraft=JSON.stringify(workflowCommand(this.workflowAction,this.workflowValues),null,2);
        this.commandNote="本步骤输入已准备；核对下方内容后请求核心确认。尚未提交。";
        this.outcome=null;
      } catch(error) {this.commandNote=error instanceof Error ? error.message : "流程输入无效"; this.outcome=null;}
      this.render();
    };
    const useReceipt=document.createElement("button"); useReceipt.textContent="从本次成功回执填入记录编号";
    useReceipt.disabled=this.pending || this.outcome?.status!=="ok" || this.outcome?.action==="doctor";
    useReceipt.onclick=()=>{
      const result=this.outcome?.response?.["result"] as Record<string,unknown> | undefined;
      if (result) {
        if (this.outcome?.action==="prepare_run" && result["status"]==="prepared"
            && typeof result["prepared_run_id"]==="string") {
          this.workflowValues["prepared_run_id"]=result["prepared_run_id"];
        }
        if (this.outcome?.action==="prepare_execution" && typeof result["execution_action_id"]==="string") {
          this.workflowValues["execution_action_id"]=result["execution_action_id"];
        }
        const facts=(result["execution_facts"] ?? (this.outcome?.action==="register_run" ? result : null)) as Record<string,unknown> | null;
        if (facts && facts["project_id"]===this.project?.project_id) {
          for (const key of ["run_id","snapshot_commit_id"]) {
            if (typeof facts[key]==="string") {
              this.workflowValues[key==="snapshot_commit_id" ? "base_snapshot_commit_id" : key]=facts[key];
            }
          }
          const attempts=facts["current_attempt_by_step"] as Record<string,unknown> | undefined;
          const attempt=attempts?.[this.workflowValues["step_id"] ?? ""];
          if (typeof attempt==="string") {this.workflowValues["attempt_id"]=attempt;}
        }
      }
      this.commandNote="已填入回执明确给出的编号；步骤选择和业务意图仍需核对。";
      this.render();
    };
    section.append(fill,useReceipt);
    return section;
  }

  /** 已提交事件增量刷新（设计 §12）：展示真实事件行与游标，界面不重算业务顺序。 */
  private eventStreamSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "event-stream";
    const title = document.createElement("h2"); title.textContent = "已提交事件（增量续读）";
    const cursor = document.createElement("p");
    cursor.className = "muted";
    cursor.dataset.testid = "event-cursor";
    cursor.textContent = this.eventCursor === null
      ? "尚未取得游标（首次读取从头开始）。"
      : `当前游标：${this.eventCursor}`;
    section.append(title, cursor);
    const outcome = this.eventOutcome;
    const resultStatus = (outcome?.response?.["result"] as Record<string, unknown> | undefined)
      ?.["status"];
    if (
      outcome !== null
      && (outcome.status !== "ok" || (typeof resultStatus === "string" && resultStatus !== "ok"))
    ) {
      const failed = document.createElement("p");
      failed.className = "muted";
      failed.textContent = this.eventNote ?? "核心未通过。";
      section.append(failed);
    }
    if (this.eventLog.length === 0) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.dataset.testid = "event-empty";
      empty.textContent = outcome === null
        ? "尚未读取事件。"
        : this.eventNote ?? "回执里没有事件行（空结果如实显示，不补造）。";
      section.append(empty);
      return section;
    }
    const list = document.createElement("ul");
    for (const row of this.eventLog) {
      const item = document.createElement("li");
      const sequence = row.eventSequence === null ? "?" : String(row.eventSequence);
      const commit = row.commitSequence === null ? "?" : String(row.commitSequence);
      const revision = row.revision === null ? "?" : String(row.revision);
      item.dataset.eventSequence = sequence;
      // 业务顺序看提交序号（设计 §12 只读规则）；不计算时长、不做聚合。
      item.textContent =
        `#${sequence} · 提交 ${commit} · ${row.eventType} · ${row.recordId}@${revision}`;
      list.append(item);
    }
    section.append(list);
    if (this.eventNote !== null) {
      const note = document.createElement("p");
      note.className = "muted";
      note.dataset.testid = "event-note";
      note.textContent = this.eventNote;
      section.append(note);
    }
    return section;
  }

  /** 最近一次回执的原始结果：**原样展示**，界面不重算任何业务结论。 */
  private resultSection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "last-result";
    const title = document.createElement("h2"); title.textContent = "最近回执";
    const outcome = this.outcome;
    if (outcome === null || outcome.response === undefined) {
      const empty = document.createElement("p");
      empty.className = "muted";
      empty.textContent = outcome === null
        ? "尚无回执。"
        : `未取得结果：${outcome.code ?? outcome.status}`;
      section.append(title, empty);
      return section;
    }
    const body = document.createElement("pre");
    body.dataset.testid = "last-result-body";
    body.style.whiteSpace = "pre-wrap";
    body.style.maxHeight = "240px";
    body.style.overflow = "auto";
    body.textContent = JSON.stringify(outcome.response, null, 2);
    section.append(title, body);
    return section;
  }

  /** 核心能力：只显示**真实回执**里的 supported_actions，不推断、不补造。 */
  private capabilitySection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "core-capability";
    const title = document.createElement("h2"); title.textContent = "核心能力（真实回执）";
    const outcome = this.doctorOutcome ?? (this.outcome?.action === "doctor" ? this.outcome : null);
    const line = document.createElement("p");
    if (outcome === null) {
      line.textContent = "尚未连接核心。";
      section.append(title, line);
      return section;
    }
    if (outcome.status === "ok") {
      const result = outcome.response?.["result"] as Record<string, unknown> | undefined;
      const supported = result?.["supported_actions"];
      const actions = Array.isArray(supported) ? supported.map(String) : [];
      const state = typeof result?.["status"] === "string" ? result["status"] : "OK";
      line.textContent = `核心 ${state}，当前可派发 ${actions.length} 个动作。`;
      const list = document.createElement("p");
      list.className = "muted";
      list.textContent = actions.join("、");
      section.append(title, line, list);
      return section;
    }
    line.textContent = `核心未通过：${outcome.code ?? outcome.status} — ${outcome.message ?? ""}`;
    section.append(title, line);
    return section;
  }

  /** 只读入口：可派发；写动作必须经受控用户入口，界面点击不等于确认。 */
  private readOnlySection(): HTMLElement {
    const section = document.createElement("section");
    section.dataset.testid = "read-only-actions";
    const title = document.createElement("h2"); title.textContent = "只读查询";
    const actions = document.createElement("div"); actions.className = "actions";
    // 项目作用域的查询只在宿主声明项目后可用：面板绝不猜项目身份。
    const scoped = this.project !== null;
    const entries: ReadonlyArray<
      readonly [string, string, Record<string, unknown>, "issues" | "reports" | null]
    > = [
      ["问题清单（未关闭）", "query", { view: "OPEN", limit: 20 }, "issues"],
      ["报告修订", "query", this.project?.run_id
        ? { run_id: this.project.run_id, limit: 5 }
        : { report_id: "unknown", limit: 5 }, "reports"],
      ["空间诊断", "storage_usage", {}, null],
      ["事件续读", "events", { limit: 20 }, null],
    ];
    for (const [label, action, parameters, scope] of entries) {
      const button = document.createElement("button");
      button.textContent = label;
      button.dataset.action = action;
      const isScoped = action === "query";
      button.disabled = this.pending || this.host === null || (isScoped && !scoped);
      button.title = this.host === null
        ? "不在编辑器宿主中，面板未连接核心"
        : (isScoped && !scoped)
          ? "宿主尚未声明当前项目；面板不猜项目身份"
          : "";
      button.onclick = () => {
        if (isScoped) {
          this.pendingQuery = scope;
          this.dispatchScoped(action, parameters);
        } else if (action === "events") {
          // 增量续读：有游标就带游标（不重头读），没有就不带。
          this.dispatch(action, this.eventCursor === null
            ? { limit: 20 }
            : { limit: 20, cursor: this.eventCursor });
        } else {
          this.dispatch(action, parameters);
        }
      };
      actions.append(button);
    }
    const note = document.createElement("p"); note.className = "muted";
    note.textContent =
      "只读入口：写动作必须经受控用户入口并携带授权，界面点击不等于用户确认。";
    section.append(title, actions, note);
    return section;
  }

  /** 计划层级与名字只在计划记录里；记录视图把内容包在 record 下。 */
  private pumpPlanLookup():void {
    const run=this.view("run");
    const ref=typeof run?.["plan_revision_ref"]==="string"?String(run["plan_revision_ref"]):"";
    if(!ref||this.pending||!this.available("read_view"))return;
    if(this.planLookup===ref)return;
    const at=ref.lastIndexOf("@");
    const revision=at>0?Number(ref.slice(at+1)):Number.NaN;
    if(!(at>0)||!Number.isSafeInteger(revision)||revision<1)return;
    this.planLookup=ref;
    this.planAwait=true;
    this.dispatchScoped("read_view",{view:"plan",record_id:ref.slice(0,at),revision});
  }

  /** 下一步该处理的步骤：待人工 → 待核验 → 阻塞 → 首个未执行。 */
  private attention(): {label:string; stepId:string} | null {
    const run=this.view("run"); if(!run)return null;
    const steps=Array.isArray(run["steps"])
      ? run["steps"].map(object).filter((item):item is Record<string,unknown>=>item!==undefined)
      : [];
    const describe=(id:string,word:string)=>{
      const index=steps.findIndex(step=>step["step_id"]===id);
      const name=caseKindName(steps.find(step=>step["step_id"]===id)?.["case_id"]);
      return `${word}${name?` · ${name}`:""}${index>=0?`（第 ${index+1} 步）`:""}`;
    };
    for(const [key,word] of [["manual_pending_step_ids","待人工"],["pending_verification_step_ids","待核验"],["blocked_step_ids","阻塞"]] as const){
      const id=strings(run[key])[0];
      if(id)return {label:describe(id,word),stepId:id};
    }
    const pending=steps.find(step=>step["state"]==="pending");
    if(pending&&typeof pending["step_id"]==="string")return {label:describe(pending["step_id"],"待执行"),stepId:pending["step_id"]};
    return null;
  }

  private phaseSection(): HTMLElement {
    const section=document.createElement("section");section.dataset.testid="phase-summary";
    const panel=this.view("panel");
    const run=this.view("run");
    // 运行视图已经带控制状态、档位与驱动；面板视图额外带业务结论。有哪个用哪个，不要求两个都到。
    const source=panel ?? run;
    const names:Record<string,string>={pre_run:"准备检查",runtime:"正在处理本次运行",post_run:"本次结果"};
    const state=text("div","");state.className="phase";
    const summary=object(run?.["selected_summary"]);
    const runId=this.project?.run_id ?? "";
    const base=typeof run?.["snapshot_commit_id"]==="string"?String(run["snapshot_commit_id"]):"";
    if(source){state.setAttribute("aria-current","step");
      // 标题用项目名，而不是"正在处理本次运行"这类占位语：用户要一眼看出在测什么。
      // 标题优先用核心投影过来的项目名称，其次计划名，最后退回通用标题。
      const coreName=typeof panel?.["project_name"]==="string"?String(panel["project_name"]):"";
      const title=coreName || this.project?.scope_name || this.projectName || this.planScopeName || "本次测试";
      state.append(text("h2",title),
        text("p",`${friendly(source["tier"])} · ${friendly(source["driver"])}`,"muted"));
      // 控制状态既是徽标，也是能否暂停/取消的前提：只显示当前状态下合法的动作。
      const control=String(source["control_state"] ?? "");
      const badge=text("p",friendly(control),"badge");badge.dataset.testid="run-control-state";
      badge.dataset.control=control;state.append(badge);
      // 业务结论也用徽标呈现；文案保持"业务结论：X"，便于外部断言与阅读。
      if(panel?.["business_outcome"]!=null){
        const outcome=String(panel["business_outcome"]);
        const outcomeBadge=text("p",`业务结论：${friendly(outcome)}`,"badge");
        outcomeBadge.dataset.outcome=outcome;
        state.append(outcomeBadge);
      }
      const canControl=!!runId && !!base && !!this.host;
      const send=(actionName:string)=>{
        this.commandDraft=JSON.stringify({action:actionName,intent_id:newIntent(),expected_revision:0,target:runId,parameters:{run_id:runId,base_snapshot_commit_id:base}});
        this.submitCommand();
      };
      const controls=document.createElement("div");controls.className="actions";
      if(control==="running")controls.append(action("暂停",()=>send("pause_run"),!canControl));
      if(control==="paused"||control==="pause_requested")controls.append(action("继续",()=>send("resume_run"),!canControl));
      if(["not_started","running","paused","pause_requested","pending_verification"].includes(control))
        controls.append(action("取消",()=>send("cancel_run"),!canControl));
      const attention=this.attention();
      controls.append(action("打开当前步骤",()=>{this.selectedStep=attention?.stepId ?? null;this.navigate("workbench");},!this.host));
      state.append(controls);
      const autoTotal=Number(run?.["automatic_step_count"] ?? 0);
      const autoDone=Number(run?.["automatic_completed_count"] ?? 0);
      if(Number.isFinite(autoTotal) && autoTotal>0){
        const bar=document.createElement("div");bar.className="progress";
        const fill=document.createElement("div");
        fill.style.width=`${Math.round((Math.min(autoDone,autoTotal)/autoTotal)*100)}%`;bar.append(fill);
        state.append(text("p",`自动步骤 ${autoDone}/${autoTotal}`,"muted"),bar);
      }
      if(attention)state.append(text("p",`待你操作：${attention.label}`));
      if(control==="running")state.append(text("p","活动执行在关闭编辑器后由核心继续管理。","muted"));
    }else{
      state.append(text("h2",this.project?.run_id?"读取本次测试":"准备本次测试"),text("p",this.project?.run_id?"读取已保存的运行，查看进度与下一步。":"从项目和测试目标开始，按提示准备检查计划。","muted"));
      // 空状态也必须有一条能走的路：此前只有依赖能力协商的"刷新本次视图"，未协商完就会卡住。
      if(this.project?.run_id){
        const read=action("读取本次运行",()=>this.dispatchScoped("read_view",{view:"run",run_id:this.project?.run_id}),!this.host);
        read.className="primary";state.append(read);
      }
    }
    // 统计收进折叠区：默认展开会让侧栏一屏放不下，但数据必须能查到。
    const stats=document.createElement("details");stats.className="technical";
    stats.append(text("summary","完整统计与来源"));
    if(summary){
      const n=(key:string)=>String(summary[key] ?? "未知");
      const total=n("denominator");
      stats.append(
        text("p",`执行 ${n("executed_count")}/${total} · 复用 ${n("reused_count")}/${total} · 验证 ${n("verified_count")}/${total}`,"muted"),
        text("p",`未验证 ${n("unverified_count")} · 已确认失败 ${n("failed_count")}`,"muted"));
    } else stats.append(text("p","读取运行详情后显示本轮执行与核验计数。","muted"));
    const result=object(this.viewOutcomes.get("panel")?.response?.["result"]);
    const note=text("p",result ? `已保存视图 · 提交 ${String(result["commit_id"])} · 事件边界 ${String(result["event_cursor"])}` : "尚未取得当前运行的准确视图。","record-id");note.dataset.testid="panel-view-boundary";
    stats.append(note);section.append(state,stats);
    const refresh=action("刷新本次视图",()=>this.dispatchScoped("read_view",{view:"panel",run_id:this.project?.run_id}),!this.available("read_view")||!this.project?.run_id);
    refresh.dataset.testid="refresh-panel-view";
    if(this.project?.run_id)section.append(refresh);
    return section;
  }

  private actionSection(): HTMLElement {
    const section=document.createElement("div");section.dataset.testid="primary-actions";section.className="actions";
    const button=action(this.project?.run_id ? "打开本次工作台" : "开始第一次测试",()=>this.navigate(this.project?.run_id ? "workbench":"setup"),!this.host);button.className="primary";
    // 侧栏放不下宽版工作台：提供在编辑器标签页里打开同一视图的入口。
    const inEditor=action("在编辑器中打开",()=>this.host?.postMessage({kind:"openDetail",detail:"workbench"}),!this.host);
    inEditor.dataset.testid="open-workbench-panel";
    section.append(button,inEditor);return section;
  }

  private detailSection(): HTMLElement {
    const section = document.createElement("section");
    const title = document.createElement("h2"); title.textContent = "详情";
    const links = document.createElement("div"); links.className = "details";
    for (const [id, label] of details) {
      const button = document.createElement("button");
      button.textContent = label; button.dataset.detail = id;
      button.onclick = () => { this.detail = id; this.render(); };
      links.append(button);
    }
    section.append(title, links);
    return section;
  }

  private detailView(): HTMLElement {
    const selected=details.find(([id])=>id===this.detail)!;
    const section=document.createElement("section");section.dataset.testid="detail-view";
    const back=action("返回本次测试",()=>this.navigate("current"));back.dataset.action="back";
    const heading=document.createElement("div");heading.className="detail-heading";
    heading.append(back,text("h1",this.detail==="settings"?"模型 API 设置":this.detail==="setup"?"准备本次测试":selected[1]));section.append(heading);
    if(this.detail==="diagnostics")section.append(this.detailSection());
    if(this.detail==="setup")section.append(this.onboarding.view(String(this.doctorOutcome?.response?.["workspace_id"]??""),this.pending));
    else if(this.detail==="review")section.append(this.reportActions(),...this.reviewDetail());
    else if(this.detail==="diagnostics")section.append(action("连接核心",()=>this.ensureCore(),this.pending||!this.host),action("刷新核心状态",()=>this.dispatch("doctor"),this.pending||!this.host),text("p",this.statusText(),"muted"),this.capabilitySection(),this.controlledSection(),this.readOnlySection(),this.eventStreamSection(),this.resultSection());
    else if(this.detail==="workbench"){
      // 工作台要知道自己在处理哪个用例：层级与名称来自计划记录，必测与否来自运行视图。
      const runView=this.view("run");
      const runSteps=Array.isArray(runView?.["steps"])
        ? runView["steps"].map(object).filter((item):item is Record<string,unknown>=>item!==undefined) : [];
      // 没选步骤时直接落在"该处理的那一步"，省掉"先点一下步骤"这一步。
      const activeStep=this.selectedStep ?? this.attention()?.stepId ?? null;
      this.selectedStep=activeStep;
      const current=runSteps.find(item=>item["step_id"]===activeStep);
      const caseId=typeof current?.["case_id"]==="string"?String(current["case_id"]):"";
      const stepLabel=[this.planLayers[caseId],caseKindName(caseId)].filter(Boolean).join(" · ");
      const isRequired=!!caseId && strings(runView?.["required_case_ids"]).includes(caseId);
      // 工作台顶部一行来路：当前运行、计划修订、当前尝试，和设计稿的三段式抬头一致。
      const runRef=typeof runView?.["run_id"]==="string"?String(runView["run_id"]):"";
      const attemptRef=typeof current?.["current_attempt_id"]==="string"?String(current["current_attempt_id"]):"";
      const contextLine=[runRef?`当前运行 ${runRef.slice(0,14)}…`:"",
        this.planRevision?`计划修订 ${this.planRevision}`:"",
        attemptRef?`当前尝试 ${attemptRef.slice(-6)}`:""].filter(Boolean).join(" · ");
      if(contextLine)section.append(text("p",contextLine,"muted"));
      section.append(this.guided.render(runView,activeStep,
        name=>this.available(name)&&!!this.project?.run_id, command=>{
          this.commandDraft=JSON.stringify({action:command.action,intent_id:command.intent,expected_revision:0,target:command.target,parameters:command.parameters});this.submitCommand();
        },()=>this.dispatchScoped("read_view",{view:"run",run_id:this.project?.run_id}),stepLabel,isRequired,this.planBasis[caseId] ?? ""),this.pendingSection(),this.caseTree());
    }
    else if(this.detail==="plan")section.append(text("p","先核对准确的计划、范围和用例修订，发布与运行授权分别确认。","muted"),
      action("读取已保存计划",()=>{this.pendingQuery="plans";this.dispatchScoped("query",{aggregate_kind:"plan",limit:20});},!this.available("query")),
      this.rowsBlock("计划修订",this.planOutcome,"plan-records"),this.caseTree());
    else section.append(this.settingsSection());
    if(this.commandNote && this.detail!=="diagnostics") {const status=text("p",this.commandNote);status.setAttribute("role","status");section.append(status);}
    if(this.selectedRecord && this.recordOutcome)section.append(this.recordDetail());
    return section;
  }

  private settingsSection():HTMLElement {
    const section=document.createElement("section");
    section.append(this.modelSettings.view(!!this.host));
    const advanced=document.createElement("details");advanced.className="technical";
    advanced.append(text("summary","项目、测试环境与本地数据"),text("p","连接已配置的测试核心后，即可读取当前项目、计划和运行。"),
      action("新建测试",()=>this.navigate("setup")),
      action("连接测试核心",()=>this.ensureCore(),this.pending||!this.host),
      action("打开插件设置",()=>this.host?.postMessage({kind:"openSettings"}),!this.host),
      text("p","高级设置用于已有核心连接和数据目录。","muted"));section.append(advanced);
    return section;
  }

  private reportActions():HTMLElement {
    const section=document.createElement("section");
    const run=this.view("run");
    const snapshot=run?.["snapshot_commit_id"];
    section.append(
      text("p","保存本次运行的真实结果。未完成的检查和证据缺口会保留在报告中。"),
      text("p",run
        ? `当前快照 commit-${snapshot==null?"未知":String(snapshot)}；同一运行重复保存沿用同一报告标识，正文变化追加内容修订。`
        : "先读取本次运行，再保存报告。","muted"),
      action("保存本次测试报告",()=>this.saveReport(),!this.available("create_report")||!run));
    const status=text("p",this.pending?"正在处理，请稍候。":this.commandNote);status.setAttribute("role","status");section.append(status);
    return section;
  }

  /** 报告摘要：先给结论与下一步，再给覆盖表与缺口，最后才是字段细节。 */
  private reportSummary(record:Record<string,unknown>, revision:number):HTMLElement {
    const block=document.createElement("section");block.dataset.testid="report-summary";
    const decision=object(record["decision"]) ?? {};
    const context=object(record["context"]) ?? {};
    const coverage=object(decision["coverage"]) ?? {};
    const list=(key:string):unknown[]=>Array.isArray(coverage[key])?coverage[key] as unknown[]:[];
    const selected=list("selected"),required=list("required");
    const executed=list("executed"),reused=list("reused"),verified=list("verified"),passed=list("passed");
    const failures=Array.isArray(decision["decisive_failures"])?decision["decisive_failures"]:[];
    const counted=(scope:unknown[],pool:unknown[])=>scope.filter(item=>pool.includes(item)).length;
    const outcome=record["business_outcome"] ?? decision["business_outcome"];
    const grade=record["evidence_grade"] ?? decision["evidence_grade"];
    const badge=document.createElement("p");badge.className="report-outcome";
    badge.textContent=`${friendly(outcome)}${grade?` · 证据 ${String(grade)}`:""}`;
    block.append(badge);
    const gaps=Array.isArray(decision["gaps"])?decision["gaps"]:[];
    const primary=object(decision["primary_gap"]) ?? object(gaps[0]);
    const reason=primary?gapText(primary["code"],primary["safe_reason"]):"";
    block.append(text("p",reason||"没有影响结论的缺口。",reason?"":"muted"));
    const actions=document.createElement("div");actions.className="actions";
    // 本地复核写成一条独立记录，不修改报告正文；结论与说明都由人填。
    const reviewBox=document.createElement("div");reviewBox.className="form-part";reviewBox.hidden=true;
    const conclusion=document.createElement("select");
    for(const value of ["本地复核通过","本地复核不通过","需要补充证据"]){
      const option=document.createElement("option");option.value=value;option.textContent=value;conclusion.append(option);
    }
    const explanation=document.createElement("textarea");
    explanation.placeholder="说明你是怎么核对的，例如：在本地重跑同一输入，持久化结果与预期一致。";
    const submitReview=action("记录本地复核",()=>{
      const detail=explanation.value.trim();
      if(!detail){explanation.focus();return;}
      this.commandDraft=JSON.stringify({action:"record_local_review",intent_id:newIntent(),expected_revision:0,
        parameters:{review_id:`review-${newIntent().slice(0,24)}`,report_id:this.selectedRecord!.record_id,report_revision:revision,conclusion:conclusion.value,explanation:detail}});
      this.submitCommand();
    },!this.host);
    reviewBox.append(text("p","复核结论","muted"),conclusion,text("p","说明","muted"),explanation,submitReview);
    actions.append(
      action("去核验",()=>{this.selectedStep=this.attention()?.stepId ?? null;this.navigate("workbench");},!this.host),
      action("本地复核",()=>{reviewBox.hidden=!reviewBox.hidden;},!this.host),
      action("导出 Markdown 摘要",()=>this.requestOperation("export_report",{report_id:this.selectedRecord!.record_id,report_revision:revision,kind:"markdown_summary",review_ids:[]}),!this.available("export_report")),
      action("导出证据包",()=>this.requestOperation("export_report",{report_id:this.selectedRecord!.record_id,report_revision:revision,kind:"evidence_bundle",review_ids:[]}),!this.available("export_report")));
    block.append(actions,reviewBox);
    // 三个分母相同的计数并排放，比一句长文本更容易一眼比较。
    const stats=document.createElement("div");stats.className="stat-row";stats.dataset.testid="report-stats";
    const cell=(label:string,value:string)=>{
      const box=document.createElement("div");box.className="stat";
      box.append(text("b",value),text("span",label));return box;
    };
    stats.append(
      cell("本轮执行",`${executed.length}/${selected.length}`),
      cell("有效复用",`${reused.length}/${selected.length}`),
      cell("有效验证",`${verified.length}/${selected.length}`));
    block.append(stats);
    // 覆盖与结论：窄侧栏用文字行，宽页面（编辑器标签页）用表格，两者同时存在由 CSS 决定。
    const scopeLine=(label:string,scope:unknown[])=>
      `${label}：通过 ${counted(scope,passed)} · 失败 ${scope.filter(item=>failures.includes(item)).length} · 未验证 ${scope.filter(item=>!verified.includes(item)).length}`;
    const lines=document.createElement("div");lines.className="coverage-lines";
    lines.append(text("p",scopeLine("本轮所选",selected)),text("p",scopeLine("完整必测",required)));
    const table=document.createElement("table");table.className="coverage-table";
    const head=document.createElement("tr");
    for(const label of ["范围","有效通过","整用例失败","未验证"]){const th=document.createElement("th");th.textContent=label;head.append(th);}
    table.append(head);
    const row=(label:string,scope:unknown[])=>{
      const tr=document.createElement("tr");
      const cells=[label,String(counted(scope,passed)),String(scope.filter(item=>failures.includes(item)).length),String(scope.filter(item=>!verified.includes(item)).length)];
      cells.forEach((value,index)=>{const cell=document.createElement(index===0?"th":"td");cell.textContent=value;tr.append(cell);});
      return tr;
    };
    table.append(row("本轮所选",selected),row("完整必测",required));
    block.append(text("h3","覆盖与结论"),lines,table);
    const gapList=Array.isArray(decision["gaps"])?decision["gaps"]:[];
    block.append(text("h3","需要补齐的证据"));
    if(gapList.length===0)block.append(text("p","没有缺口。","muted"));
    else for(const raw of gapList){
      const item=object(raw);
      block.append(text("p",gapText(item?.["code"],item?.["safe_reason"])));
      const hint=gapHint(item?.["code"]);
      if(hint)block.append(text("p",hint,"hint"));
    }
    const scope=typeof context["scope_name"]==="string"?String(context["scope_name"]):"";
    if(scope)block.append(text("p",`范围：${scope}`,"muted"));
    // 结果来源：只列报告里真实存在的来源字段，缺哪项就少哪项。
    const source=typeof context["source_identity"]==="string"?String(context["source_identity"]):"";
    const environment=typeof context["environment_ref"]==="string"
      ? `环境 ${String(context["environment_ref"]).replace(/^env-/,"").split("@")[0].slice(0,8)}` : "";
    const planRefs=Array.isArray(context["plan_revision_refs"])?context["plan_revision_refs"]:[];
    const lastPlan=planRefs.length?String(planRefs[planRefs.length-1]):"";
    const planRevision=lastPlan.includes("@")?lastPlan.slice(lastPlan.lastIndexOf("@")+1):"";
    const origin=[source?`源码 ${source.replace(/^sha256:/,"").slice(0,8)}`:"",environment,planRevision?`计划修订 ${planRevision}`:""].filter(Boolean);
    if(origin.length)block.append(text("p",`结果来源：${origin.join(" · ")}`,"muted"));
    block.append(text("p","此为报告冻结快照；核验更新将形成新的报告修订。","muted"));
    return block;
  }

  private recordDetail():HTMLElement {
    const section=document.createElement("section");section.dataset.testid="record-detail";
    const view=object(object(this.recordOutcome?.response?.["result"])?.["view"]);
    const record=object(view?.["record"]);
    if(!record || !this.selectedRecord)return section;
    section.append(text("h2",`${this.selectedRecord.aggregate_kind==="report"?"测试报告":"已保存内容"} · 修订 ${this.selectedRecord.revision}`),text("p","已保存快照 · 不随当前运行变化","muted"));
    if(this.selectedRecord.aggregate_kind==="report")section.append(this.reportSummary(record,this.selectedRecord.revision));
    const labels:Record<string,string>={business_outcome:"业务结论",decision:"判定与覆盖",context:"范围与来源",created_at:"保存时间",evidence_refs:"证据索引",scope:"验收范围",cases:"用例",objective:"目标",expected:"预期",actual:"实际",state:"状态",title:"标题",run_tier:"检查档位",initial_driver:"执行方式",status:"状态",required_case_ids:"必测用例",preconditions:"前置条件",steps:"检查步骤",verification_method:"核验方式",independent_verification:"独立核验",importance:"重要程度"};
    Object.assign(labels,{evidence_grade:"证据等级",primary_gap:"首要缺口",gaps:"未完成原因",safe_reason:"原因",selected_summary:"本轮所选范围",required_summary:"冻结必测范围",denominator:"范围内用例",executed_count:"已执行",reused_count:"已复用",verified_count:"有效验证",passed_count:"通过",failed_count:"失败",unverified_count:"未验证",decisive_failure_count:"已确认失败",scope_kind:"统计范围",project_name:"项目",scope_name:"测试范围",tier:"检查档位",driver:"执行方式",inputs:"输入",layer:"检查层级",name:"名称",description:"说明",evidence_refs:"证据引用"});
    const present=(value:unknown):HTMLElement=>{
      if(Array.isArray(value)){const list=document.createElement("ul");for(const item of value){const li=document.createElement("li");li.append(present(item));list.append(li);}return list;}
      const values=object(value);if(values){const wrapper=document.createElement("div");const dl=document.createElement("dl");dl.className="facts";const more=document.createElement("details");more.className="technical";more.append(text("summary","查看字段与引用"));for(const [key,child] of Object.entries(values)){const dd=document.createElement("dd");dd.append(present(child));if(key in labels)dl.append(text("dt",labels[key]!),dd);else more.append(text("dt",key),dd);}wrapper.append(dl);if(more.childElementCount>1)wrapper.append(more);return wrapper;}
      return text("span",value==null?"未提供":friendly(value));
    };
    const technical=document.createElement("details");technical.append(text("summary","技术来源（排障用）"));
    const autoOpen=this.selectedRecord.aggregate_kind==="report" ? ["scope"] : ["business_outcome","decision","scope","cases","objective"];
    for(const [key,value] of Object.entries(record)){if(!(key in labels)){technical.append(text("p",key),present(value));continue;}const group=document.createElement("details");group.open=autoOpen.includes(key);group.append(text("summary",labels[key]??key),present(value));section.append(group);}
    section.append(technical);
    return section;
  }

  /** 问题与报告：把**真实只读回执**里的记录行原样列出，不重算结论。 */
  private reviewDetail(): HTMLElement[] {
    const blocks: HTMLElement[] = [];
    const actions = document.createElement("div"); actions.className = "actions";
    const issueButton = document.createElement("button");
    issueButton.textContent = "读取问题清单";
    issueButton.dataset.action = "query";
    issueButton.dataset.scope = "issues";
    issueButton.disabled = this.pending || this.host === null || this.project === null;
    issueButton.onclick = () => {
      this.pendingQuery = "issues";
      this.dispatchScoped("query", { view: "OPEN", limit: 20 });
    };
    const reportButton = document.createElement("button");
    reportButton.textContent = "读取报告修订";
    reportButton.dataset.action = "query";
    reportButton.dataset.scope = "reports";
    reportButton.disabled = this.pending || this.host === null || this.project === null
      || !this.project.run_id;
    reportButton.onclick = () => {
      if (this.project?.run_id) {
        this.pendingQuery = "reports";
        this.dispatchScoped("query", { run_id: this.project.run_id, limit: 5 });
      }
    };
    actions.append(issueButton, reportButton);
    blocks.push(actions);
    if (this.project === null) {
      const blocked = document.createElement("p"); blocked.className = "muted";
      blocked.dataset.testid = "review-blocked";
      blocked.textContent = "宿主尚未声明当前项目：面板不猜项目身份，问题与报告读取保持不可用。";
      blocks.push(blocked);
      return blocks;
    }
    blocks.push(
      this.rowsBlock("问题清单（未关闭）", this.issueOutcome, "review-issues", true),
      this.rowsBlock("报告修订", this.reportOutcome, "review-reports"),
      this.deliveryBlock(),
    );
    if (this.issueDetail !== null) {
      blocks.push(this.issueDetailBlock(this.issueDetail));
    }
    return blocks;
  }

  private deliveryBlock(): HTMLElement {
    const section=document.createElement("section"); section.dataset.testid="delivery-verification";
    const title=document.createElement("h2"); title.textContent="交付说明与实际验证";
    section.append(title);
    for (const [key,label] of [["delivery_id","读取交付编号"],["submission_id","正式提测编号（可留空）"]] as const) {
      const field=document.createElement("label"); field.textContent=label;
      const input=document.createElement("input"); input.setAttribute("aria-label",label);
      input.value=this.deliveryLookup[key]; input.disabled=this.pending;
      input.oninput=()=>{this.deliveryLookup[key]=input.value;this.viewOutcomes.delete("delivery");
        section.querySelector('[data-testid="delivery-result"]')?.remove();};
      field.append(input); section.append(field);
    }
    const read=document.createElement("button"); read.textContent="读取交付验证";
    const capabilities=this.doctorOutcome?.response?.["result"] as Record<string,unknown>|undefined;
    read.disabled=this.pending || this.host===null || !this.project
      || !Array.isArray(capabilities?.["supported_actions"]) || !capabilities["supported_actions"].includes("read_view");
    read.onclick=()=>{
      if (!this.deliveryLookup.delivery_id.trim()) {return;}
      this.viewOutcomes.delete("delivery");
      this.dispatchScoped("read_view",{view:"delivery",delivery_id:this.deliveryLookup.delivery_id.trim(),
        ...(this.deliveryLookup.submission_id.trim() ? {submission_id:this.deliveryLookup.submission_id.trim()}: {})});
    };
    section.append(read);
    const result=this.viewOutcomes.get("delivery")?.response?.["result"] as Record<string,unknown>|undefined;
    const view=result?.["view"] as Record<string,unknown>|undefined;
    const submission=view?.["submission"] as Record<string,unknown>|null|undefined;
    if (!view || view["delivery_id"]!==this.deliveryLookup.delivery_id.trim()
        || (this.deliveryLookup.submission_id.trim()
          ? submission?.["submission_id"]!==this.deliveryLookup.submission_id.trim()
          : view["submitted"]!==false)) {return section;}
    const values=document.createElement("div");values.dataset.testid="delivery-result";
    for (const [label,key] of [
      ["开发自述完成","self_report_completed"],["开发自述未完成","self_report_incomplete"],
      ["实际验证范围","verified_in_scope"],["仍未验证范围","unverified_scope"],
      ["核验状态","verification_state"],["业务结论","verification_business_outcome"],
      ["核验报告编号","verification_report_id"],
    ] as const) {
      const row=document.createElement("p");const value=view[key];
      const states:Record<string,string>={verification_valid:"核验有效",material_invalid:"材料无效",
        required_verification_incomplete:"必要核验未完成",passed:"通过",failed:"失败",
        incomplete:"未完成",not_applicable:"不适用"};
      const text=typeof value==="string" && ["verification_state","verification_business_outcome"].includes(key)
        ? `${states[value] ?? "未知"}（${value}）` : value==null ? "待核实"
          : Array.isArray(value) ? value.join("、") || "无" : String(value);
      row.textContent=`${label}：${text}`;
      values.append(row);
    }
    const note=document.createElement("p");note.textContent="开发自述、核验有效与业务通过分别展示。";
    values.append(note);section.append(values);return section;
  }

  /** 问题详情（设计 §10/§12）：**只读**回执原样展示 + 复制说明（不执行修复）。 */
  private issueDetailBlock(row: Row): HTMLElement {
    const block = document.createElement("div");
    block.dataset.testid = "issue-detail";
    const heading = document.createElement("h2");
    heading.textContent = `问题详情 ${row.record_id}@${row.revision}`;
    block.append(heading);
    const outcome = this.issueDetailOutcome;
    // 复测简报只需要**问题身份**（来自清单回执），不依赖详情载荷：
    // 详情回执缺失或只含记录行时，简报入口同样可用。
    const brief = this.briefBlock(row);
    if (outcome === null) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "尚未读取该问题详情。";
      block.append(empty, this.copyButton(row, null), brief);
      return block;
    }
    if (outcome.status !== "ok") {
      const failed = document.createElement("p"); failed.className = "muted";
      failed.textContent =
        `核心未通过 · ${outcome.code ?? outcome.status}：${outcome.message ?? ""}`;
      block.append(failed, this.copyButton(row, null), brief);
      return block;
    }
    const result = (outcome.response?.["result"] ?? {}) as Record<string, unknown>;
    const nested = result[String(result["aggregate_kind"] ?? "")];
    // 只有回执**真的带回一条记录**时才算详情载荷：B `query` 目前只回
    // `{aggregate_kind, record_id, revision}` 行（列表读摘要），此时不得把
    // 分页对象当成问题记录，也不得让"复制说明"复制一份页码。
    const candidate = result["record"] ?? nested;
    const record = (
      candidate !== undefined && candidate !== null && typeof candidate === "object"
        ? candidate
        : null
    ) as Record<string, unknown> | null;
    if (record === null) {
      const rowsOnly = document.createElement("p");
      rowsOnly.className = "muted";
      rowsOnly.dataset.testid = "issue-detail-rows-only";
      rowsOnly.textContent =
        "该回执只含记录行（列表读摘要），未包含详情载荷：字段不推断，详情读取需按引用取。";
      block.append(rowsOnly);
    }
    const fields = document.createElement("dl");
    for (const [key, label] of issueFields) {
      const value = record?.[key];
      const term = document.createElement("dt"); term.textContent = label;
      const detail = document.createElement("dd");
      detail.dataset.field = key;
      // 回执里没有该字段时如实说明"未提供"，绝不按状态推断内容。
      detail.textContent = value === undefined || value === null
        ? "回执未提供该字段（不推断）。"
        : typeof value === "string" ? value : JSON.stringify(value);
      fields.append(term, detail);
    }
    const raw = document.createElement("pre");
    raw.dataset.testid = "issue-detail-body";
    raw.style.whiteSpace = "pre-wrap";
    raw.style.maxHeight = "200px";
    raw.style.overflow = "auto";
    raw.textContent = JSON.stringify(outcome.response, null, 2);
    block.append(fields, this.copyButton(row, record), raw, brief);
    return block;
  }

  /**
   * 复测简报（只读，`copy_repair_brief`）：按**当前问题身份**与宿主声明的 run 读取，
   * 简报正文与元数据原样展示；未知位置与排除项如实列出（设计 §10「复制说明不执行修复」同理）。
   */
  private briefBlock(row: Row): HTMLElement {
    const wrapper = document.createElement("div");
    wrapper.dataset.testid = "repair-brief";
    const read = document.createElement("button");
    read.textContent = "读取复测简报";
    read.dataset.action = "copy_repair_brief";
    read.disabled = this.pending || this.host === null || this.project === null;
    read.title = this.project === null
      ? "宿主尚未声明当前项目；面板不猜项目身份"
      : "按当前问题身份读取复测简报（只读，不修改任何记录）";
    read.onclick = () => {
      const parameters: Record<string, unknown> = { issue_id: row.record_id };
      if (this.project?.run_id) { parameters["run_id"] = this.project.run_id; }
      this.briefIssueId = row.record_id;
      this.briefOutcome = null;
      this.briefNote = null;
      this.dispatchScoped("copy_repair_brief", parameters);
    };
    wrapper.append(read);
    const outcome = this.briefOutcome;
    if (outcome === null) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.dataset.testid = "repair-brief-empty";
      empty.textContent = "尚未读取复测简报。";
      wrapper.append(empty);
      return wrapper;
    }
    if (outcome.status !== "ok") {
      const failed = document.createElement("p"); failed.className = "muted";
      failed.dataset.testid = "repair-brief-error";
      failed.textContent =
        `核心未通过 · ${outcome.code ?? outcome.status}：${outcome.message ?? ""}`
        + "（未取得简报正文）";
      wrapper.append(failed);
      return wrapper;
    }
    const result = (outcome.response?.["result"] ?? {}) as Record<string, unknown>;
    const brief = result["brief"];
    const body = document.createElement("pre");
    body.dataset.testid = "repair-brief-body";
    body.style.whiteSpace = "pre-wrap";
    body.style.maxHeight = "240px";
    body.style.overflow = "auto";
    // 简报正文原样展示；回执没给正文时如实说明，不用别的内容代替。
    body.textContent = typeof brief === "string" && brief.length > 0
      ? brief
      : "回执未提供简报正文（不补造）。";
    const meta = document.createElement("dl");
    const metaFields: ReadonlyArray<readonly [string, string]> = [
      ["facts_state", "事实状态"],
      ["unknown_positions", "待核实位置"],
      ["excluded", "已排除项"],
      ["redaction_policy_version", "脱敏策略"],
    ];
    for (const [key, label] of metaFields) {
      const value = result[key];
      const term = document.createElement("dt"); term.textContent = label;
      const detail = document.createElement("dd");
      detail.dataset.briefField = key;
      detail.textContent = value === undefined || value === null
        ? "回执未提供该字段（不推断）。"
        : typeof value === "string" ? value : JSON.stringify(value);
      meta.append(term, detail);
    }
    const copy = document.createElement("button");
    copy.textContent = "复制简报";
    copy.dataset.action = "copy-brief";
    copy.disabled = typeof brief !== "string" || brief.length === 0;
    copy.title = "复制简报正文；本按钮不派发核心动作、不执行修复";
    copy.onclick = () => {
      const clipboard = (globalThis as {
        navigator?: { clipboard?: { writeText?: (v: string) => Promise<void> } };
      }).navigator?.clipboard;
      if (clipboard?.writeText && typeof brief === "string") {
        void clipboard.writeText(brief);
        this.briefNote = "已复制简报（未派发核心动作、未执行修复）。";
      } else {
        this.briefNote = "当前环境没有剪贴板或回执无正文：未复制，也未执行任何动作。";
      }
      this.render();
    };
    wrapper.append(body, meta, copy);
    if (this.briefNote !== null) {
      const note = document.createElement("p"); note.className = "muted";
      note.dataset.testid = "brief-note";
      note.textContent = this.briefNote;
      wrapper.append(note);
    }
    return wrapper;
  }

  /** 复制说明：只把回执里的文字放进剪贴板，**不派发任何动作**（设计 §10）。 */
  private copyButton(row: Row, record: Record<string, unknown> | null): HTMLElement {
    const wrapper = document.createElement("div");
    const button = document.createElement("button");
    button.textContent = "复制说明";
    button.dataset.action = "copy-description";
    button.disabled = record === null;
    button.title = record === null
      ? "回执未包含详情载荷：没有可复制的说明，本按钮不猜内容"
      : "复制该问题的说明文字；本按钮不执行修复、不派发核心动作";
    button.onclick = () => {
      const text = typeof record?.["description"] === "string"
        ? String(record["description"])
        : JSON.stringify(record ?? {}, null, 2);
      const clipboard = (globalThis as { navigator?: { clipboard?: { writeText?: (v: string) => Promise<void> } } })
        .navigator?.clipboard;
      if (clipboard?.writeText) {
        void clipboard.writeText(text);
        this.copyNote = "已复制说明（未执行修复、未派发核心动作）。";
      } else {
        this.copyNote = "当前环境没有剪贴板权限：未复制，也未执行任何动作。";
      }
      this.render();
    };
    wrapper.append(button);
    if (this.copyNote !== null) {
      const note = document.createElement("p");
      note.className = "muted";
      note.dataset.testid = "copy-note";
      note.textContent = this.copyNote;
      wrapper.append(note);
    }
    return wrapper;
  }

  /** 一个只读列表块：只渲染回执里的 record_id/修订，缺回执即如实说明。 */
  private rowsBlock(
    titleText: string, outcome: Outcome | null, testId: string, selectable = false,
  ): HTMLElement {
    const block = document.createElement("div");
    block.dataset.testid = testId;
    const heading = document.createElement("h2"); heading.textContent = titleText;
    block.append(heading);
    if (outcome === null) {
      const empty = document.createElement("p"); empty.className = "muted";
      empty.textContent = "尚未读取。";
      block.append(empty);
      return block;
    }
    if (outcome.status !== "ok") {
      const failed = document.createElement("p"); failed.className = "muted";
      failed.textContent = `核心未通过 · ${outcome.code ?? outcome.status}：${outcome.message ?? ""}`;
      block.append(failed);
      return block;
    }
    const rows = rowsOf(outcome.response);
    if (rows.length === 0) {
      const none = document.createElement("p"); none.className = "muted";
      none.textContent = "回执里没有记录行（空结果如实显示，不补造）。";
      block.append(none);
      return block;
    }
    const list = document.createElement("ul");
    for (const row of rows) {
      const item = document.createElement("li");
      item.dataset.recordId = row.record_id;
      if (selectable) {
        // 可选中：点击按**回执行给出的身份**读取该记录详情（不猜 id、不猜修订）。
        const open = document.createElement("button");
        open.textContent = `${row.record_id}@${row.revision}（${row.aggregate_kind}）`;
        open.dataset.action = "open-issue";
        open.dataset.recordId = row.record_id;
        open.disabled = this.pending || this.project === null;
        open.onclick = () => {
          this.issueDetail = row;
          this.issueDetailOutcome = null;
          this.copyNote = null;
          this.pendingQuery = "issue-detail";
          this.dispatchScoped("query", {
            aggregate_kind: "issue", record_id: row.record_id, revision: row.revision, limit: 1,
          });
        };
        item.append(open);
      } else if(["plan","report","case"].includes(row.aggregate_kind)) {
        item.append(action(`${row.record_id} · 修订 ${row.revision}`,()=>{
          this.selectedRecord=row;this.recordOutcome=null;
          this.dispatchScoped("read_view",{view:row.aggregate_kind,record_id:row.record_id,revision:row.revision});
        },!this.available("read_view")));
      } else {
        item.textContent = `${row.record_id}@${row.revision}（${row.aggregate_kind}）`;
      }
      list.append(item);
    }
    block.append(list);
    return block;
  }
}

const CASE_KIND_NAMES:Record<string,string>={syntax:"加载与语法",normal:"正常输入",boundary:"边界输入",error:"异常输入"};

function caseKindName(id:unknown):string {
  if(typeof id!=="string")return "";
  return CASE_KIND_NAMES[id.slice(id.lastIndexOf("-")+1)] ?? "";
}

/** 导出回执带 artifact_ref（相对用户数据工作空间）；直接告知位置，避免用户无从查找。 */
function exportNote(result:Record<string,unknown>|null|undefined):string {
  const ref=result?.["artifact_ref"];
  if(typeof ref!=="string"||!ref.startsWith("artifacts/"))return "导出请求已完成，可在诊断中查看制品位置。";
  return `导出已完成：${ref}（相对用户数据工作空间）`;
}

customElements.define("ai-test-panel", AITestPanel);
