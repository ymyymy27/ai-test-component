/** Presentation state only; core receipts remain authoritative. */
import {newIntent, action, object, strings, text, gapText, gapHint} from "./shell";

export type GuidedCommand = {action:string; parameters:Record<string,unknown>; target?:string; intent:string};
type Execution = {intent:string; actionId?:string; authorized:boolean; executed?:boolean};
export class GuidedRun {
  private executions = new Map<string,Execution>();
  private waiting: {step:string; action:string}|null=null;
  private note="";
  private needsRefresh=false;
  private conclusions=new Map<string,string>();
  reset():void {this.executions.clear();this.conclusions.clear();this.waiting=null;this.note="";this.needsRefresh=false;}
  receive(name:string,status:string,result:Record<string,unknown>|undefined):void {
    if(name==="read_view" && status==="ok" && result?.["view_type"]==="run")this.needsRefresh=false;
    if(!this.waiting || this.waiting.action!==name)return;
    const current=this.executions.get(this.waiting.step);
    if(status!=="ok")this.note="本次操作未完成。可重试同一操作；不会自动重新执行测试。";
    else if(name==="prepare_execution" && current && typeof result?.["execution_action_id"]==="string"){
      current.actionId=result["execution_action_id"];this.note="操作已解析。请查看核心确认窗口中的操作内容和影响，再决定是否授权。";
    }else if(name==="authorize_step" && current && typeof result?.["authorization_id"]==="string" && result["step_id"]===this.waiting.step && result["intent_id"]===current.intent){
      // A transport receipt alone is insufficient; execution still checks the saved grant.
      current.authorized=true;this.note="授权请求已返回；执行时核心仍会核对已保存授权。";
    }else if(name==="execute_step"){if(current)current.executed=true;this.needsRefresh=true;this.note="执行请求已返回。请刷新步骤，按最新结果进行独立核验。";}
    else if(name==="verify_pending"){
      // 核验回执带 verification.observation（匹配/不匹配/无结果），此前未展示。
      const verification=object(result?.["verification"]);
      const observation=verification?.["observation"];
      if(this.waiting && typeof observation==="string")this.conclusions.set(this.waiting.step,observation);
      this.note="核验请求已返回。刷新后查看实际核验结论与未完成原因。";
    }
    this.waiting=null;
  }
  render(run:Record<string,unknown>|undefined,selected:string|null,enabled:(name:string)=>boolean,
    send:(command:GuidedCommand)=>void,refresh:()=>void,label="",required=false,basis=""):HTMLElement {
    const section=document.createElement("section");section.dataset.testid="guided-run";
    section.append(text("h2","按步骤完成测试"));
    if(!run){section.append(text("p","先读取本次运行，插件会自动取得步骤和核验所需信息。"),action("读取本次运行",refresh,!enabled("read_view")));return section;}
    const steps=Array.isArray(run["steps"])?run["steps"].map(object).filter(Boolean):[];
    const step=steps.find(item=>item?.["step_id"]===selected);
    if(!step){section.append(text("p","在下方用例列表中选择要处理的步骤。"),action("刷新步骤",refresh,!enabled("read_view")));return section;}
    const id=String(step["step_id"]);
    let execution=this.executions.get(id);
    if(!execution){execution={intent:newIntent(),authorized:false};this.executions.set(id,execution);}
    const request=(name:string,parameters:Record<string,unknown>,intent=execution!.intent)=>{
      this.waiting={step:id,action:name};send({action:name,parameters,target:id,intent});
    };
    section.append(text("h3",`第 ${steps.indexOf(step)+1} 步${label?` · ${label}`:""}${required?" · 必测":""}`));
    const facts=document.createElement("dl");facts.className="facts";
    const conclusion=this.conclusions.get(id);
    facts.append(text("dt","步骤状态"),text("dd",friendly(step.state)),text("dt","本次尝试"),text("dd",step.current_attempt_id?friendly(step.attempt_state):"尚无执行记录"),text("dt","核验结论"),text("dd",conclusion?friendly(conclusion):"尚未核验"),text("dt","断言依据"),text("dd",basis?friendly(basis):"待核实"),text("dt","依据"),text("dd",step.invalidated===true?"已过期，需要处理后重新核对":"以已保存计划为准"));section.append(facts);
    // 这一步为什么还没通过：直接用核心给出的缺口代码，面板只做中文说明。
    const gaps=strings(step["gap_ids"]);
    if(gaps.length){
      const block=document.createElement("section");block.className="workbench-part gap-box";
      block.dataset.testid="step-gaps";
      block.append(text("h3","当前缺口"));
      for(const code of gaps){
        block.append(text("p",gapText(code,"")));
        const hint=gapHint(code);
        if(hint)block.append(text("p",hint,"hint"));
      }
      block.append(text("p","接口响应成功不能代替持久化核验；请记录实际操作与同一业务对象的证据。","hint"));
      section.append(block);
    }
    const operation=document.createElement("section");operation.className="workbench-part";
    operation.append(text("h3","执行操作"),text("p","先查看操作内容和影响，再确认并执行。","muted"));
    let primary:HTMLButtonElement|undefined;
    if(!execution.actionId)primary=action("查看将执行的操作",()=>request("prepare_execution",{run_id:run["run_id"],step_id:id}),!enabled("prepare_execution"));
    else if(!execution.authorized)primary=action("查看并确认操作授权",()=>request("authorize_step",{execution_action_id:execution!.actionId,record_revision:1}),!enabled("authorize_step"));
    else if(!execution.executed)primary=action("执行已确认的操作",()=>request("execute_step",{execution_action_id:execution!.actionId,record_revision:1}),!enabled("execute_step"));
    if(primary){primary.className="primary";operation.append(primary);}else operation.append(text("p","执行请求已返回，请读取最新结果。","muted"));
    section.append(operation);
    const verification=document.createElement("section");verification.className="workbench-part";
    verification.append(text("h3","结果核验"),text("p","按独立预期核对本次实际执行。执行完成和验证通过分别记录。","muted"));
    const attempt=step["current_attempt_id"];
    const canVerify=!this.needsRefresh && typeof attempt==="string" && attempt && typeof run["snapshot_commit_id"]==="string";
    if(canVerify){
      // 核验是"执行完成后唯一的下一步"：设成主按钮，避免用户以为执行完就结束了。
      const verify=action("核验这次执行结果",()=>request("verify_pending",{run_id:run["run_id"],step_id:id,attempt_id:attempt,base_snapshot_commit_id:run["snapshot_commit_id"]},`verify-${execution!.intent}-${String(run["snapshot_commit_id"]).slice(-32)}`),!enabled("verify_pending"));
      verify.className="primary";
      verification.append(verify);
    }
    else verification.append(text("p",this.needsRefresh?"先核实执行状态，取得本次执行的最新记录。":"尚无可核验的执行记录，请先完成执行。","muted"));
    section.append(verification);
    const checkState=action("核实执行状态",refresh,!enabled("read_view"));
    // 需要先刷新时，把"核实执行状态"作为主按钮，形成一条明确路径：核实 → 核验。
    if(this.needsRefresh)checkState.className="primary";
    section.append(checkState);
    if(this.note){const status=text("p",this.note,"notice");status.setAttribute("role","status");section.append(status);}
    return section;
  }
}

export function friendly(value:unknown):string {
  const labels:Record<string,string>={passed:"通过",failed:"失败",incomplete:"未完成",not_applicable:"不适用",full:"完整验证",quick:"快速检查",on_demand:"所选检查",planned:"按计划执行",stepwise:"逐步执行",running:"运行中",paused:"已暂停",completed:"已完成",pending:"待处理",blocked:"受阻",cancelled:"已取消",ready:"准备就绪",verified:"已核验",unverified:"待核验",matched:"匹配",mismatched:"不匹配",no_result:"无结果",pending_verification:"待核验",not_started:"未开始",pause_requested:"暂停请求中",cancelling:"取消中",missing:"未填",present_unconfirmed:"未确认",confirmed:"已确认"};
  return value==null?"待核实":labels[String(value)]??String(value);
}
