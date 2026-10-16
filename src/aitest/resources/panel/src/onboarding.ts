import {action,newIntent,object,text} from "./shell";

type Command={key:string;action:string;intent_id:string;expected_revision:number;binding_revision:number;target?:string;parameters:Record<string,unknown>};
type Check={objective:string;inputs:string;key:string;value:string;type:string};
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
  private model="deepseek-chat";
  private modelTimeout="60";
  private useModel=true;
  private confirmed=false;
  private dataPolicy="";
  private workspace="";
  private suggestions="";
  private openCheck=0;
  private checks:Check[]=["加载与语法","正常输入","边界输入","异常输入"].map(objective=>({objective,inputs:"",key:"",value:"",type:"text"}));
  constructor(private send:(message:unknown)=>void,private submit:(command:Command)=>void,
    private refresh:()=>void,private render:()=>void,private complete:()=>void){}
  private host(kind:string,extra:Record<string,unknown>={}):void{
    this.request="wizard-"+newIntent();this.busy=true;this.note="正在准备，请稍候…";
    this.send({kind,clientRequestId:this.request,...extra});this.render();
  }
  isConfiguring():boolean{return this.stage==="configure";}
  receive(data:Record<string,unknown>):boolean {
    if(!data||typeof data!=="object")return false;
    if(!this.request||data.kind!=="wizardResult"||data.clientRequestId!==this.request)return false;
    this.request=null;this.busy=false;
    if(data.error){this.note=String(data.error);this.render();return true;}
    if(data.cancelled){this.note="已取消，尚未继续。";this.render();return true;}
    const session=object(data.session);
    if(session){this.session=session;if(this.stage==="choose"){this.name=String(session.name??"");this.endpoint=String(session.endpoint??this.endpoint);this.model=String(session.model??this.model);this.modelTimeout=String(session.timeout??this.modelTimeout);this.stage="configure";this.note="已选择项目，请填写测试目标。";}}
    if(data.credential){if(this.session)this.session.reference=data.reference;this.note="密钥已保存到系统安全存储。";}
    if(data.setup){this.stage="project";this.note="独立测试环境已准备，正在连接核心。";this.refresh();}
    if(Array.isArray(data.commands)){
      this.queue=data.commands as Command[];this.index=0;this.next();
    }
    this.render();return true;
  }
  outcome(name:string,status:string,result:Record<string,unknown>|undefined):void {
    if(name==="doctor" && status==="ok"){this.render();return;}
    const command=this.queue[this.index];if(!this.busy||!command||command.action!==name)return;
    this.busy=false;
    if(status!=="ok" || !result || result.blocked===true || result.published===false&&name==="publish_plan"){
      this.note="这一步尚未完成。请处理确认窗口或所缺材料，再继续原步骤。";this.render();return;
    }
    this.receipts[command.key]=result;
    if(command.key==="suggestions")this.suggestions=plain(result.draft_text??"模型未返回可展示的草稿正文");
    this.index++;this.next();
  }
  private next():void {
    const command=this.queue[this.index];
    if(command){this.busy=true;this.note=`正在处理第 ${this.index+1}/${this.queue.length} 项，请核对需要确认的内容。`;this.submit(command);return;}
    this.queue=[];this.note="本步已保存。";
    if(this.queueStage==="project")this.stage=this.useModel?"model":"plan";
    else if(this.queueStage==="model")this.stage="plan";
    else if(this.queueStage==="plan"){this.start("prepare");return;}
    else if(this.queueStage==="prepare"){this.start("register");return;}
    else if(this.queueStage==="register"){this.stage="done";this.note="测试计划与运行已准备。打开工作台，确认操作后开始执行。";}
    this.render();
  }
  private form():Record<string,unknown> {
    const checks=this.checks.map(check=>{
      let value:unknown=check.value;
      if(check.type==="number"){value=Number(check.value);if(!check.value.trim()||!Number.isFinite(value))throw new Error("请填写有效的预期数字。");}
      if(check.type==="boolean"){if(!["true","false"].includes(check.value))throw new Error("是/否预期请填写 true 或 false。");value=check.value==="true";}
      return {objective:check.objective,inputs:check.inputs,expected:{[check.key]:value}};
    });
    return {project_id:this.session?.project_id,workspace_id:this.workspace,source:this.session?.source,
      entry:this.session?.entry,name:this.name.trim(),goal:this.goal.trim(),data_policy:this.dataPolicy.trim(),
      dependency_declaration:"独立 Python 3.13 环境，仅标准库",endpoint:this.endpoint,model:this.model,
      model_timeout:Number(this.modelTimeout),
      checks:this.queueStage==="plan"||["prepare","register"].includes(this.queueStage)?checks:[]};
  }
  private start(stage:string):void {
    this.queueStage=stage;
    try{
      if(stage==="plan" && this.checks.some(check=>!check.inputs.trim()||!check.key.trim()||!check.value.trim()))throw new Error("请补齐四项检查的输入条件和独立预期结果。");
      this.host("wizardCommands",{stage,form:this.form(),receipts:this.receipts});
    }catch(error){this.note=error instanceof Error?error.message:"请核对表单。";this.render();}
  }
  view(workspace:string,busy:boolean):HTMLElement {
    this.workspace=workspace;
    const section=document.createElement("section");section.dataset.testid="onboarding";
    const steps=document.createElement("ol");steps.className="wizard-steps";steps.setAttribute("aria-label","测试准备进度");
    const active=this.stage==="choose"?0:this.stage==="configure"?this.configurationPage:this.stage==="project"?2:3;
    ["选择项目","模型 API","测试环境","核对计划"].forEach((label,index)=>{const item=text("li",`${index+1} ${label}`);if(active===index)item.setAttribute("aria-current","step");steps.append(item);});
    section.append(steps);
    const progress=document.createElement("p");progress.className="muted subtle";
    const stageLabels:Record<string,string>={choose:"选择项目",configure:"配置模型与环境",project:"保存项目",model:"准备检查建议",plan:"核对并发布计划",done:"开始执行"};
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
    else if(this.stage==="model")section.append(text("h3","确认模型使用范围"),text("p",`接收方：${this.endpoint}；模型：${this.model}`),text("p","只发送项目名称与测试目标，不发送源码或运行证据。模型只返回建议，不会执行程序。"),
      text("p",this.goal),action("确认发送并生成检查建议",()=>this.start("model"),locked||frozen),action("暂不使用模型，人工填写",()=>{this.queue=[];this.stage="plan";this.render();},locked));
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
    if(this.queue[this.index]&&!locked)section.append(action("继续当前步骤",()=>this.next()));
    const status=text("p",this.note,this.note?"notice":"");status.setAttribute("role","status");section.append(status);return section;
  }
}
function plain(value:unknown):string {
  if(typeof value==="string")return value;
  if(Array.isArray(value))return value.map(plain).join("\n");
  const values=object(value);return values?Object.values(values).map(plain).join("\n"):"";
}
