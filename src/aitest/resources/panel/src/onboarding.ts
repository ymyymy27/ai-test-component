import {action,newIntent,object,text} from "./shell";

type Command={key:string;action:string;intent_id:string;expected_revision:number;binding_revision:number;target?:string;parameters:Record<string,unknown>};
type Check={objective:string;inputs:string;key:string;value:string;type:string};
// Mirrors the extension's plain-write allowlist: actions that are neither human
// confirmations nor challenge actions.  Keep in sync with core-bridge.ts.
const PLAIN_WRITE_ACTIONS=new Set([
  "save_context","save_case","save_task","save_delivery","save_acceptance",
  "save_dependency_graph","save_project_view_preference","save_rules",
  "generate_draft","request_model_draft","resolve_model_response","save_plan",
  "analyze_project","prepare_run","register_run","prepare_execution","execute_step",
  "start_run","pause_run","resume_run","cancel_run","retry_step","return_stage",
  "verify_pending","attach_evidence","record_agent_evidence","import_external_result",
  "create_report","export_report","record_fix","request_regression",
]);
export class Onboarding {
  private session:Record<string,unknown>|null=null;
  private request:string|null=null;
  private stage="choose";
  private configurationPage=0;
  private queue:Command[]=[];
  private index=0;
  private queueStage="";
  private receipts:Record<string,unknown>={};
  private busy=false;
  private note="";
  private name="";
  private goal="";
  private endpoint="https://api.deepseek.com";
  private model="deepseek-flash";
  private modelTimeout="60";
  private useModel=true;
  private confirmed=false;
  private dataPolicy="";
  private workspace="";
  private suggestions="";
  private openCheck=0;
  private checks:Check[]=["加载与语法","正常输入","边界输入","异常输入"].map(objective=>({objective,inputs:"",key:"",value:"",type:"text"}));
  private proposal:Record<string,unknown>|null=null;
  private preview:Record<string,unknown>|null=null;
  private reviewIssues:{path:string;message:string}[]=[];
  private runTier="full";
  private catalogConfirmed=false;
  private driver="planned";
  private commitAfterReview=false;
  constructor(private send:(message:unknown)=>void,private submit:(command:Command)=>void,
    private submitWrite:(command:Command)=>void,
    private refresh:()=>void,private render:()=>void,private complete:()=>void){}
  private deliver(command:Command):void {
    if(PLAIN_WRITE_ACTIONS.has(command.action))this.submitWrite(command);
    else this.submit(command);
  }
  private host(kind:string,extra:Record<string,unknown>={}):void{
    this.request="wizard-"+newIntent();this.busy=true;this.note="正在准备，请稍候…";
    this.send({kind,clientRequestId:this.request,...extra});this.render();
  }
  isConfiguring():boolean{return this.stage==="configure";}
  restart():void {
    // 失败后无入口可重来：清空本轮向导状态，回到选择项目；不触碰已保存的项目或运行。
    this.stage="choose";this.configurationPage=0;this.request=null;this.busy=false;
    this.queue=[];this.index=0;this.queueStage="";this.receipts={};this.session=null;
    this.name="";this.goal="";this.dataPolicy="";this.confirmed=false;this.workspace="";
    this.suggestions="";this.openCheck=0;
    this.proposal=null;this.preview=null;this.reviewIssues=[];this.runTier="full";this.driver="planned";this.catalogConfirmed=false;
    this.checks=["加载与语法","正常输入","边界输入","异常输入"].map(objective=>({objective,inputs:"",key:"",value:"",type:"text"}));
    this.note="已重新开始，请重新选择项目。";this.render();
  }
  receive(data:Record<string,unknown>):boolean {
    if(!data||typeof data!=="object")return false;
    if(!this.request||data.kind!=="wizardResult"||data.clientRequestId!==this.request)return false;
    this.request=null;this.busy=false;
    if(data.error){this.commitAfterReview=false;this.note=String(data.error);this.render();return true;}
    if(data.cancelled){this.commitAfterReview=false;this.note="已取消，尚未继续。";this.render();return true;}
    if(data.valid===false){
      this.commitAfterReview=false;
      const issues=Array.isArray(data.issues)?data.issues:[];
      this.reviewIssues=issues.map(item=>{const value=object(item)??{};return {path:String(value.path??"$"),message:String(value.message??"字段不合法")};});
      this.note="计划还有需要修正的字段，请按提示修改后重新校验。";this.render();return true;
    }
    const preview=object(data.preview);
    if(preview){
      this.preview=preview;const proposed=object(preview.proposal);
      if(proposed)this.proposal=proposed;
      this.stage="review";this.reviewIssues=[];
      if(this.commitAfterReview){this.commitAfterReview=false;this.start("commit");return true;}
      this.note="请核对 AI 生成的计划；全部字段可改。";
      this.render();return true;
    }
    const session=object(data.session);
    if(session){this.session=session;if(this.stage==="choose"){this.name=String(session.name??"");this.endpoint=String(session.endpoint??this.endpoint);this.model=String(session.model??this.model);this.modelTimeout=String(session.timeout??this.modelTimeout);this.stage="configure";this.note="已选择项目，请填写测试目标。";}}
    if(data.credential){if(this.session)this.session.reference=data.reference;this.note="密钥已保存到系统安全存储。";}
    if(data.setup){this.stage="project";this.note="独立测试环境已准备，正在连接核心。";this.refresh();}
    if(Array.isArray(data.commands)){
      this.queue=data.commands as Command[];this.index=0;this.next();
    }
    this.render();return true;
  }
  outcome(name:string,status:string,result:Record<string,unknown>|undefined,code?:string,message?:string):void {
    if(name==="doctor" && status==="ok"){this.render();return;}
    const command=this.queue[this.index];if(!this.busy||!command||command.action!==name)return;
    this.busy=false;
    if(command.key==="suggestions" && result && result.proposal_error){
      this.proposal=null;this.preview=null;
      this.note="模型返回的计划结构不合规。可重试生成，或改用手工四项检查。";this.render();return;
    }
    if(status!=="ok" || !result || result.blocked===true || result.status==="blocked" || result.published===false&&name==="publish_plan"){
      // 固定文案无法区分超时取消、被拒绝与参数错误；回执带原因时如实展示。
      const reason=(message??"").trim();
      this.note=reason?`这一步尚未完成：${reason}${code?`（${code}）`:""}`:"这一步尚未完成。请处理确认窗口或所缺材料，再继续原步骤。";
      this.render();return;
    }
    this.receipts[command.key]=result;
    if(command.key==="suggestions"){
      this.suggestions=plain(result.draft_text??"模型未返回可展示的草稿正文");
      const proposed=object(result.proposal);
      if(proposed)this.proposal=proposed;
    }
    this.index++;this.next();
  }
  private next():void {
    const command=this.queue[this.index];
    if(command){this.busy=true;this.note=`正在处理第 ${this.index+1}/${this.queue.length} 项，请核对需要确认的内容。`;this.deliver(command);return;}
    this.queue=[];this.note="本步已保存。";
    if(this.queueStage==="project")this.stage=this.useModel?"model":"plan";
    else if(this.queueStage==="model"){
      if(this.proposal)this.startReview();
      else this.stage="plan";
    }
    else if(this.queueStage==="plan"){this.start("prepare");return;}
    else if(this.queueStage==="commit"){this.start("prepare");return;}
    else if(this.queueStage==="prepare"){this.start("register");return;}
    else if(this.queueStage==="register"){this.stage="done";this.note="测试计划与运行已准备。打开工作台，确认操作后开始执行。";}
    this.render();
  }
  private startReview(commit=false):void {
    this.queueStage="review";this.commitAfterReview=commit;
    try{this.host("wizardReview",{form:this.form()});}
    catch(error){this.commitAfterReview=false;this.note=error instanceof Error?error.message:"请核对表单。";this.render();}
  }
  private form():Record<string,unknown> {
    const usingProposal=this.proposal!==null;
    const checks=usingProposal?[]:this.checks.map(check=>{
      let value:unknown=check.value;
      if(check.type==="number"){value=Number(check.value);if(!check.value.trim()||!Number.isFinite(value))throw new Error("请填写有效的预期数字。");}
      if(check.type==="boolean"){if(!["true","false"].includes(check.value))throw new Error("是/否预期请填写 true 或 false。");value=check.value==="true";}
      return {objective:check.objective,inputs:check.inputs,expected:{[check.key]:value}};
    });
    return {project_id:this.session?.project_id,workspace_id:this.workspace,source:this.session?.source,
      entry:this.session?.entry,name:this.name.trim(),goal:this.goal.trim(),data_policy:this.dataPolicy.trim(),
      dependency_declaration:"独立 Python 3.13 环境，仅标准库",endpoint:this.endpoint,model:this.model,
      model_timeout:Number(this.modelTimeout),proposal:this.proposal,
      understanding:object(this.receipts["snapshot"])?.["understanding"]??null,
      coverage_contract:this.preview?.["coverage_contract"]??null,
      understanding_ref:this.preview?.["understanding_ref"]??null,
      catalog_confirmed:this.catalogConfirmed,
      run_tier:this.runTier,initial_driver:this.driver,
      checks:this.proposal?[]:(this.queueStage==="plan"||["prepare","register"].includes(this.queueStage)?checks:[])};
  }
  private start(stage:string):void {
    this.queueStage=stage;
    try{
      if(stage==="plan" && this.checks.some(check=>!check.inputs.trim()||!check.key.trim()||!check.value.trim()))throw new Error("请补齐四项检查的输入条件和独立预期结果。");
      this.host("wizardCommands",{stage,form:this.form(),receipts:this.receipts});
    }catch(error){this.note=error instanceof Error?error.message:"请核对表单。";this.render();}
  }
  private reviewView(section:HTMLElement,locked:boolean,frozen:boolean):void {
    const proposal=this.proposal;
    if(!proposal){section.append(text("p","尚未收到 AI 计划提案，请返回模型步骤重新生成。","notice"));return;}
    const scope=object(proposal.scope)??{};
    const raw=Array.isArray(proposal.cases)?proposal.cases:[];
    const cases=raw.map(item=>object(item)??{});
    const disabled=locked||frozen;
    const field=(label:string,value:string,change:(value:string)=>void,multiline=false)=>{
      const row=document.createElement("label");row.append(text("span",label));
      const input=multiline?document.createElement("textarea"):document.createElement("input");
      input.value=value;input.disabled=disabled;input.setAttribute("aria-label",label);
      input.oninput=()=>change(input.value);row.append(input);return row;
    };
    const list=(label:string,values:unknown,change:(values:string[])=>void)=>
      field(label,Array.isArray(values)?values.map(String).join("\n"):"",value=>change(value.split(/\r?\n/).map(item=>item.trim()).filter(Boolean)),true);
    const ids=(label:string,values:unknown,change:(values:string[])=>void)=>
      field(label,Array.isArray(values)?values.map(String).join(", "):"",value=>change(value.split(/[,，\n]/).map(item=>item.trim()).filter(Boolean)));
    const select=(label:string,value:string,options:string[],change:(value:string)=>void)=>{
      const row=document.createElement("label");row.append(text("span",label));
      const element=document.createElement("select");element.disabled=disabled;
      for(const option of options){const node=document.createElement("option");node.value=option;node.textContent=option;element.append(node);}
      element.value=value;element.onchange=()=>change(element.value);row.append(element);return row;
    };
    section.append(text("h3","核对 AI 计划"),text("p","全部字段可改；服务端校验仍是权威。发布后计划冻结，运行中只影响未执行步骤。","muted"));
    const coverage=object(this.preview?.["coverage"]);
    if(coverage){
      const box=document.createElement("div");
      const templateItems=Number(coverage["template_items"]??0),coveredItems=Number(coverage["covered_items"]??0),requiredItems=Number(coverage["required_items"]??0);
      box.append(text("h4","覆盖收据"),text("p",`模板项 ${templateItems} · 已覆盖 ${coveredItems} · 必测 ${requiredItems}`,"muted"));
      section.append(box);
      const rows=Array.isArray(this.preview?.["coverage_preview"])?this.preview["coverage_preview"] as unknown[]:[];
      if(rows.length){
        const table=document.createElement("table");table.className="coverage-table";table.dataset.testid="plan-coverage";
        const head=document.createElement("tr");for(const label of ["业务功能","总体覆盖","缺口"])head.append(text("th",label));table.append(head);
        for(const rawRow of rows){
          const row=object(rawRow)??{};const tr=document.createElement("tr");
          tr.append(text("td",String(row.function_id??"")),text("td",String(row.overall_status??"")),text("td",String(row.gap_reason??"")));
          table.append(tr);
        }
        section.append(table);
      }
    }
    const goals=Array.isArray(this.preview?.["goal_candidates"])?this.preview["goal_candidates"] as unknown[]:[];
    if(goals.length){
      const box=document.createElement("div");box.append(text("h4","本次验证目标"),text("p","AI 按变更、影响和历史推荐；你可以取消或补选。取消后对应的覆盖声明会一并移除。","muted"));
      const selected=new Set(Array.isArray(proposal.goal_intent_ids)?proposal.goal_intent_ids.map(String):[]);
      for(const rawGoal of goals){
        const goal=object(rawGoal)??{};const id=String(goal.intent_id??"");if(!id)continue;
        const label=document.createElement("label");label.className="goal-choice";
        const check=document.createElement("input");check.type="checkbox";check.checked=selected.has(id);check.disabled=disabled;
        check.setAttribute("aria-label",`验证目标 ${String(goal.statement??id)}`);
        check.onchange=()=>{
          const ids=new Set(Array.isArray(proposal.goal_intent_ids)?proposal.goal_intent_ids.map(String):[]);
          if(check.checked)ids.add(id);else ids.delete(id);
          proposal.goal_intent_ids=[...ids];
          if(Array.isArray(proposal.coverage_claims))proposal.coverage_claims=(proposal.coverage_claims as unknown[]).filter(item=>{const claim=object(item)??{};return ids.has(String(claim.intent_id??""));});
          this.render();
        };
        label.append(check,text("span",String(goal.statement??id)),text("small",String(goal.why??""),"muted"));
        box.append(label);
      }
      section.append(box);
    }
    const unknownCount=Number(this.preview?.["unknown_count"]??0),gapCount=Number(this.preview?.["capability_gap_count"]??0);
    const catalog=document.createElement("label");const catalogCheck=document.createElement("input");catalogCheck.type="checkbox";catalogCheck.checked=this.catalogConfirmed;catalogCheck.disabled=disabled;
    catalogCheck.setAttribute("aria-label","确认业务功能目录完整");catalogCheck.onchange=()=>{this.catalogConfirmed=catalogCheck.checked;this.render();};
    catalog.append(catalogCheck,document.createTextNode(`我已确认业务功能目录完整，unknown 数已知（unknown ${unknownCount} · 能力缺口 ${gapCount}）`));
    section.append(catalog);
    section.append(field("范围名称",String(scope.name??""),value=>scope.name=value),
      field("范围目标",String(scope.objective??""),value=>scope.objective=value,true));
    section.append(select("档位",this.runTier,["quick","on_demand","full"],value=>this.runTier=value),
      select("驱动",this.driver,["planned","stepwise"],value=>this.driver=value));
    const advanced=document.createElement("details");advanced.className="technical";
    advanced.append(text("summary","高级范围字段（JSON）"));
    advanced.append(field("scope 其余字段",JSON.stringify({
      required_case_ids:scope.required_case_ids??[],excluded_case_ids:scope.excluded_case_ids??[],
      exclusion_reasons:scope.exclusion_reasons??[],dependency_closure_ids:scope.dependency_closure_ids??[],
      applicability_exclusions:scope.applicability_exclusions??[]},null,2),
      value=>{try{const parsed=JSON.parse(value);if(parsed&&typeof parsed==="object"&&!Array.isArray(parsed))Object.assign(scope,parsed);}catch{/* keep last valid */}},true));
    section.append(advanced);
    if(this.reviewIssues.length){
      const box=document.createElement("div");box.className="notice";box.append(text("strong","需要修正"));
      for(const issue of this.reviewIssues)box.append(text("p",`${issue.path}：${issue.message}`));
      section.append(box);
    }
    cases.forEach((item,index)=>{
      const fold=document.createElement("details");fold.className="check-form";fold.open=index===0;
      fold.append(text("summary",`${index+1}. ${String(item.case_id??"")} · 模板项 ${String(item.template_item_id??"")}`));
      const body=document.createElement("div");body.className="form-part";
      body.append(text("p",`用例 ${String(item.case_id??"")}（只读）/ 模板项 ${String(item.template_item_id??"")}（只读）`,"muted"),
        select(`用例 ${index+1} 层级`,String(item.layer??"L2"),["L1","L2","L3"],value=>item.layer=value),
        field(`用例 ${index+1} 目标`,String(item.objective??""),value=>item.objective=value),
        list(`用例 ${index+1} 前置条件（每行一条）`,item.preconditions,value=>item.preconditions=value),
        list(`用例 ${index+1} 输入（每行一条）`,item.inputs,value=>item.inputs=value),
        list(`用例 ${index+1} 步骤（每行一条）`,item.steps,value=>item.steps=value),
        field(`用例 ${index+1} 预期`,String(item.expected??""),value=>item.expected=value,true),
        field(`用例 ${index+1} 核验方式`,String(item.verification_method??""),value=>item.verification_method=value),
        field(`用例 ${index+1} 断言依据`,String(item.assertion_basis_text??""),value=>item.assertion_basis_text=value,true),
        field(`用例 ${index+1} 独立核验`,String(item.independent_verification??""),value=>item.independent_verification=value,true));
      const links=object(item.links)??{};
      body.append(ids(`用例 ${index+1} 验收项 ID（逗号分隔）`,links.acceptance_item_ids,value=>links.acceptance_item_ids=value),
        ids(`用例 ${index+1} 模块 ID（逗号分隔）`,links.module_ids,value=>links.module_ids=value),
        ids(`用例 ${index+1} 关键链路 ID（逗号分隔）`,links.critical_path_ids,value=>links.critical_path_ids=value),
        field(`用例 ${index+1} 无关键链路理由`,String(links.no_critical_path_reason??""),value=>links.no_critical_path_reason=value),
        list(`用例 ${index+1} Mock 范围（每行一条）`,item.mock_scope,value=>item.mock_scope=value),
        select(`用例 ${index+1} 重要性`,String(item.importance??"P1"),["P0","P1","P2"],value=>item.importance=value));
      fold.append(body);section.append(fold);
    });
    section.append(action("重新校验",()=>this.startReview(false),disabled),
      action("校验并发布",()=>this.startReview(true),disabled));
  }
  view(workspace:string,busy:boolean):HTMLElement {
    this.workspace=workspace;
    const section=document.createElement("section");section.dataset.testid="onboarding";
    const steps=document.createElement("ol");steps.className="wizard-steps";steps.setAttribute("aria-label","测试准备进度");
    const active=this.stage==="choose"?0:this.stage==="configure"?this.configurationPage:this.stage==="project"?2:3;
    ["选择项目","模型 API","测试环境","核对计划"].forEach((label,index)=>{const item=text("li",`${index+1} ${label}`);if(active===index)item.setAttribute("aria-current","step");steps.append(item);});
    section.append(steps);
    const progress=document.createElement("p");progress.className="muted subtle";
    const stageLabels:Record<string,string>={choose:"选择项目",configure:"配置模型与环境",project:"保存项目",model:"准备检查建议",review:"核对 AI 计划",commit:"保存并发布计划",plan:"核对并发布计划",done:"开始执行"};
    progress.textContent=`当前：${stageLabels[this.stage]??"准备测试"} · 接下来按提示执行、核验并保存报告`;
    if(this.stage!=="choose"&&this.stage!=="configure")section.append(progress);
    const locked=this.busy||busy;
    const frozen=this.queue.length>0;
    const field=(label:string,value:string,change:(value:string)=>void,multiline=false,disabled=false)=>{
      const row=document.createElement("label");row.append(text("span",label));
      const input=multiline?document.createElement("textarea"):document.createElement("input");
      input.value=value;input.setAttribute("aria-label",label);input.disabled=locked||frozen||disabled;
      input.oninput=()=>change(input.value);row.append(input);return row;
    };
    if(this.stage==="choose"){
      section.append(text("h3","你想测试哪个项目？"),text("p","选择本地项目目录，再选择用于测试的 Python 文件。接下来会逐步准备模型、环境和检查计划。","muted"));
      const choose=action("选择项目目录",()=>this.host("wizardChoose"),locked);choose.className="primary";section.append(choose);
      const supported=document.createElement("details");supported.className="technical";supported.append(text("summary","支持哪些项目？"),text("p","当前向导支持仅使用标准库、输出 JSON 结果的 Python 本地测试入口。入口需包含测试场景；任意源码不能直接当作完整测试。"));section.append(supported);
      if(this.note){const note=text("p",this.note,"notice");note.setAttribute("role","status");section.append(note);}return section;
    }
    section.append(text("p",`项目：${this.name} · ${this.session?.entry??""}`));
    if(this.stage==="configure"){
      const projectPart=document.createElement("div");projectPart.className="form-part";projectPart.hidden=this.configurationPage!==0;
      projectPart.append(text("h3","告诉我们这次要检查什么"),field("项目名称",this.name,value=>this.name=value),field("你希望验证什么",this.goal,value=>this.goal=value,true),text("p","描述应该发生什么，以及哪些情况需要特别检查。例如：正常加法得到正确结果，输入无效时给出错误。"));
      const environmentPart=document.createElement("div");environmentPart.className="form-part";environmentPart.hidden=this.configurationPage!==2;
      environmentPart.append(text("h3","准备独立测试环境"),text("p","插件将创建 Python 3.13 环境，与你平时使用的环境分开。当前流程仅支持标准库，不会安装项目脚本或依赖。"),
        field("测试数据如何隔离、测试后如何复位",this.dataPolicy,value=>this.dataPolicy=value,true),text("p","例如：只在内存中计算，不连接业务系统，结束后无需清理。如果会写文件或访问数据库，请说明专用测试数据及清理方式。"));
      const confirmation=document.createElement("label"),checkbox=document.createElement("input");checkbox.type="checkbox";checkbox.checked=this.confirmed;checkbox.disabled=locked;checkbox.onchange=()=>this.confirmed=checkbox.checked;
      confirmation.append(checkbox,document.createTextNode("我确认这是本地测试入口，不会修改真实业务数据"));environmentPart.append(confirmation);
      const modelPart=document.createElement("div");modelPart.className="form-part";modelPart.hidden=this.configurationPage!==1;
      modelPart.append(text("h3","模型 API"),text("p","让模型帮助准备检查建议。编辑器聊天的登录信息不会用于这里。"));
      const choice=document.createElement("label"),ai=document.createElement("input");ai.type="checkbox";ai.checked=this.useModel;ai.disabled=locked;ai.onchange=()=>{this.useModel=ai.checked;this.render();};choice.append(ai,document.createTextNode("使用模型帮助准备检查建议"));modelPart.append(choice);
      if(this.useModel)modelPart.append(text("p","供应方：DeepSeek / OpenAI 兼容接口"),field("API 地址",this.endpoint,value=>this.endpoint=value),field("模型名称",this.model,value=>this.model=value),
        field("请求超时（秒，10–120）",this.modelTimeout,value=>this.modelTimeout=value),
        text("p",this.session?.reference?"安全凭据已选择，不显示密钥正文。":"尚未配置安全凭据。"),action("安全配置 API 密钥",()=>this.host("wizardCredential"),locked),
        text("p","默认不发送源码。首次请求只发送下面已填写的项目名称和测试目标，并另行确认。","muted"));
      section.append(projectPart,modelPart,environmentPart);
      const nav=document.createElement("div");nav.className="wizard-nav";
      if(this.configurationPage>0)nav.append(action("上一步",()=>{this.configurationPage--;this.note="";this.render();},locked));
      if(this.configurationPage<2){const next=action(this.configurationPage===0?"下一步：模型 API":"下一步：测试环境",()=>{
        if(this.configurationPage===0&&(!this.name.trim()||!this.goal.trim())){this.note="请填写项目名称和本次测试目标。";this.render();return;}
        this.configurationPage++;this.note="";this.render();
      },locked);next.className="primary";nav.append(next);}
      else {const prepare=action("准备环境并继续",()=>{
        if(!this.name.trim()||!this.goal.trim()||!this.dataPolicy.trim()||!this.confirmed){this.note="请填写项目目标、数据复位说明，并确认数据隔离。";this.render();return;}
        if(this.useModel&&(!Number.isInteger(Number(this.modelTimeout))||Number(this.modelTimeout)<10||Number(this.modelTimeout)>120)){this.note="请求超时请填写 10–120 之间的整数秒。";this.render();return;}
        this.host("wizardSetup",{dataConfirmed:this.confirmed,useModel:this.useModel,endpoint:this.endpoint});
      },locked);prepare.className="primary";nav.append(prepare);}section.append(nav);
    }else if(this.stage==="project")section.append(text("p","将保存项目、目录绑定和环境说明；目录绑定需要你确认。"),action("保存项目并继续",()=>this.start("project"),locked||frozen||!workspace));
    else if(this.stage==="model")section.append(text("h3","确认模型使用范围"),text("p",`接收方：${this.endpoint}；模型：${this.model}`),text("p","只发送项目名称、测试目标与内置模板骨架，不发送源码或运行证据。模型只返回建议，不会执行程序。"),
      text("p",this.goal),action("确认发送并生成检查建议",()=>this.start("model"),locked||frozen),action("暂不使用模型，人工填写",()=>{this.queue=[];this.proposal=null;this.preview=null;this.stage="plan";this.render();},locked));
    else if(this.stage==="review"){this.reviewView(section,locked,frozen);}
    else if(this.stage==="plan"){
      section.append(text("h3","核对检查计划"),text("p","测试入口应覆盖以下四类场景，并输出结果字段。每项会执行同一入口，输入条件是说明，不会自动传给程序。请根据需求填写独立预期；不能把运行结果直接当成正确答案。"),
        text("p","例如：输入条件填“2 加 3”，结果字段填入口约定的 normal，预期结果填 5，并选择“数字”。入口需要输出包含 normal 的 JSON 结果。","muted"));
      if(this.suggestions){const ai=document.createElement("details");ai.open=true;ai.append(text("summary","模型建议（需你核对）"),text("p",this.suggestions));section.append(ai);}
      this.checks.forEach((check,index)=>{
        const fold=document.createElement("details");fold.className="check-form";fold.open=this.openCheck===index;
        fold.append(text("summary",`${index+1}. ${check.objective}`));
        fold.ontoggle=()=>{if(fold.open)this.openCheck=index;};
        const item=document.createElement("div");item.className="form-part";item.append(
          field(`${index+1}. 输入条件`,check.inputs,value=>check.inputs=value),
          field(`${index+1}. 结果字段名称`,check.key,value=>check.key=value),
          field(`${index+1}. 预期结果`,check.value,value=>check.value=value));
        const type=document.createElement("select");type.setAttribute("aria-label",`${index+1}. 预期类型`);type.disabled=locked||frozen;
        for(const [value,label] of [["text","文字"],["number","数字"],["boolean","是/否"]]){const option=document.createElement("option");option.value=value!;option.textContent=label!;type.append(option);}type.value=check.type;type.onchange=()=>check.type=type.value;item.append(type);fold.append(item);section.append(fold);
      });
      section.append(action("确认计划并准备测试",()=>this.start("plan"),locked||frozen));
    }else section.append(action("进入测试工作台",()=>this.complete(),locked));
    if(this.stage!=="choose"&&!locked)section.append(action("重新开始",()=>this.restart()));
    if(this.queue[this.index]&&!locked)section.append(action("继续当前步骤",()=>this.next()));
    const status=text("p",this.note,this.note?"notice":"");status.setAttribute("role","status");section.append(status);return section;
  }
}
function plain(value:unknown):string {
  if(typeof value==="string")return value;
  if(Array.isArray(value))return value.map(plain).join("\n");
  const values=object(value);return values?Object.values(values).map(plain).join("\n"):"";
}
