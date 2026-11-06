import {action,object,text} from "./shell";

type Command={action:string;intent_id:string;expected_revision:number;binding_revision?:number|null;target?:string;parameters:Record<string,unknown>};
type Step={stepId:string;caseId:string;state:string};
type Resolved={stepId:string;caseId:string;actionId:string;intentId:string;requestId:string;state:string;message:string};

function stable(value:string):string {
  let hash=2166136261;
  for(let index=0;index<value.length;index++){
    hash^=value.charCodeAt(index);hash=Math.imul(hash,16777619);
  }
  return (hash>>>0).toString(16);
}

/**
 * Pre-run authorization list + planned-driver start.
 *
 * It only orchestrates existing actions: `prepare_execution` for every step,
 * one core-owned batch window for per-action `authorize_step`, then `start_run`.
 * It never signs a confirmation and never retries an unknown intent.
 */
export class RunAuthorization {
  private runId="";
  private base="";
  private steps:Step[]=[];
  private resolved:Resolved[]=[];
  private index=0;
  private busy=false;
  private note="";
  private awaitingStep="";
  private batchRequest="";

  constructor(
    private send:(message:unknown)=>void,
    private submitWrite:(command:Command)=>void,
    private render:()=>void,
    private context:()=>string,
    private requestId:()=>string,
    private refresh:()=>void,
  ){}

  reset():void {
    this.runId="";this.base="";this.steps=[];this.resolved=[];this.index=0;
    this.busy=false;this.note="";this.awaitingStep="";this.batchRequest="";
  }

  open(runView:Record<string,unknown>|null):void {
    if(!runView)return;
    const runId=typeof runView["run_id"]==="string"?runView["run_id"]:"";
    if(!runId||runId===this.runId)return;
    this.runId=runId;
    this.base=typeof runView["snapshot_commit_id"]==="string"?runView["snapshot_commit_id"]:"";
    const raw=Array.isArray(runView["steps"])?runView["steps"]:[];
    this.steps=raw.map(item=>{
      const value=object(item)??{};
      return {stepId:String(value.step_id??""),caseId:String(value.case_id??""),state:String(value.state??"pending")};
    }).filter(step=>step.stepId);
    this.resolved=[];this.index=0;this.note="";
  }

  private next():void {
    if(this.index>=this.steps.length){
      this.busy=false;this.note=`已解析 ${this.resolved.filter(item=>item.actionId).length} 个动作。打开授权窗口逐项确认。`;
      this.render();return;
    }
    const step=this.steps[this.index];
    if(step.state==="completed"||step.state==="cancelled"){
      this.index++;this.next();return;
    }
    this.awaitingStep=step.stepId;
    const intent="auth-"+stable(this.runId+"|"+step.stepId);
    this.submitWrite({action:"prepare_execution",intent_id:intent,expected_revision:0,
      target:step.stepId,parameters:{run_id:this.runId,step_id:step.stepId}});
  }

  private resolveAll():void {
    if(this.busy)return;
    this.resolved=[];this.index=0;this.busy=true;this.note="正在解析每个步骤的准确动作…";this.render();this.next();
  }

  private openBatch():void {
    if(this.busy)return;
    const items=this.resolved.filter(item=>item.actionId&&item.state!=="confirmed");
    if(!items.length){this.note="没有可授权的已解析动作；先点击“解析待授权动作”。";this.render();return;}
    this.batchRequest=this.requestId();
    this.busy=true;
    this.note="已在核心窗口列出待授权动作；请逐项确认。";
    this.send({kind:"nativeBatch",clientRequestId:this.batchRequest,contextId:this.context(),
      items:items.map(item=>({action:"authorize_step",requestId:item.requestId,
        intentId:item.intentId,expectedRevision:0,bindingRevision:null,target:item.stepId,
        parameters:{execution_action_id:item.actionId,record_revision:1}}))});
    this.render();
  }

  private start():void {
    if(this.busy||!this.runId||!this.base)return;
    this.busy=true;this.note="已请求按计划自动执行；核心会连续推进已授权步骤。";
    this.submitWrite({action:"start_run",intent_id:"start-"+stable(this.runId+"|"+this.base),
      expected_revision:0,target:this.runId,
      parameters:{run_id:this.runId,base_snapshot_commit_id:this.base}});
    this.render();
  }

  receive(action:string,status:string,result:Record<string,unknown>|undefined,code?:string,message?:string):boolean {
    if(action==="prepare_execution"){
      const stepId=this.awaitingStep;this.awaitingStep="";
      if(!stepId)return false;
      const step=this.steps.find(item=>item.stepId===stepId);
      const actionId=status==="ok"&&result&&typeof result["execution_action_id"]==="string"
        ?String(result["execution_action_id"]):"";
      this.resolved.push({
        stepId,caseId:step?.caseId??"",actionId,
        intentId:"auth-"+stable(this.runId+"|"+stepId),
        requestId:this.requestId(),
        state:actionId?"resolved":"waiting_runtime",
        message:actionId?"":"运行时才能解析或准备被阻塞"+(message?`：${message}`:code?`：${code}`:""),
      });
      this.index++;this.next();this.render();return true;
    }
    if(action==="authorize_batch"){
      this.busy=false;this.batchRequest="";
      const raw=result&&Array.isArray(result["items"])?result["items"]:[];
      for(const entry of raw){
        const item=object(entry);if(!item)continue;
        const stepId=typeof item["step_id"]==="string"?item["step_id"]:"";
        const target=this.resolved.find(value=>value.stepId===stepId);
        if(!target)continue;
        target.state=String(item["state"]??"pending");
        target.message=String(item["message"]??"");
      }
      const confirmed=this.resolved.filter(item=>item.state==="confirmed").length;
      this.note=`授权窗口已返回：${confirmed} 项已确认；未确认项不会授权。`;
      this.render();return true;
    }
    if(action==="start_run"){
      this.busy=false;
      const schedule=result&&typeof result["status"]==="string"?String(result["status"]):"";
      let note="已开始自动执行；工作台会继续刷新进度。";
      if(status!=="ok")note="启动未完成，请核对缺授权或阻塞原因。";
      else if(schedule==="waiting_authorization")note="已开始调度，但仍有步骤等待授权；补授权后会继续。";
      else if(schedule==="ambiguous_authorization")note="同一存在多个未用授权，核心拒绝自动选择；请核对授权记录。";
      this.note=note;
      if(status==="ok")this.refresh();
      this.render();return true;
    }
    return false;
  }

  view(runView:Record<string,unknown>|null):HTMLElement {
    const section=document.createElement("section");section.dataset.testid="run-authorization";
    const state=runView&&typeof runView["control_state"]==="string"?runView["control_state"]:"";
    if(!runView||!this.runId||state!=="not_started"){
      return section;
    }
    section.append(text("h3","授权并自动执行"),
      text("p","先解析每个步骤的准确动作，再在一个核心窗口里逐项确认；确认后按计划连续执行。","muted"));
    const list=document.createElement("div");list.className="run-authorization-list";
    for(const step of this.steps){
      const resolved=this.resolved.find(item=>item.stepId===step.stepId);
      const row=document.createElement("p");row.className="muted";
      const stateText=resolved?resolved.state==="confirmed"?"已授权":resolved.state==="waiting_runtime"?"待运行时解析":resolved.state==="failed"?"授权失败":resolved.actionId?"已解析":"待解析":"待解析";
      row.textContent=`${step.stepId} · ${step.caseId} · 步骤状态 ${step.state} · ${stateText}${resolved?.message?`（${resolved.message}）`:""}`;
      list.append(row);
    }
    section.append(list);
    section.append(action("解析待授权动作",()=>this.resolveAll(),this.busy),
      action("打开授权窗口",()=>this.openBatch(),this.busy||!this.resolved.some(item=>item.actionId)),
      action("开始自动执行",()=>this.start(),this.busy||!this.resolved.some(item=>item.state==="confirmed")));
    if(this.note){const note=text("p",this.note,"notice");note.setAttribute("role","status");section.append(note);}
    return section;
  }
}
