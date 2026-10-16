/** Presentation state only; core receipts remain authoritative. */
import {newIntent, action, object, text} from "./shell";

export type GuidedCommand = {action:string; parameters:Record<string,unknown>; target?:string; intent:string};
type Execution = {intent:string; actionId?:string; authorized:boolean; executed?:boolean};
export class GuidedRun {
  private executions = new Map<string,Execution>();
  private waiting: {step:string; action:string}|null=null;
  private note="";
  private needsRefresh=false;
  reset():void {this.executions.clear();this.waiting=null;this.note="";this.needsRefresh=false;}
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
    else if(name==="verify_pending")this.note="核验请求已返回。刷新后查看实际核验结论与未完成原因。";
    this.waiting=null;
  }
  render(run:Record<string,unknown>|undefined,selected:string|null,enabled:(name:string)=>boolean,
    send:(command:GuidedCommand)=>void,refresh:()=>void):HTMLElement {
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
    section.append(text("p",`当前步骤：第 ${steps.indexOf(step)+1} 步`),text("p",`步骤状态：${friendly(step["state"])}`),
      text("p","先查看操作，再确认是否允许执行。完成执行后，还需要核对结果。","muted"));
    if(!execution.actionId)section.append(action("查看将执行的操作",()=>request("prepare_execution",{run_id:run["run_id"],step_id:id}),!enabled("prepare_execution")));
    else if(!execution.authorized)section.append(action("查看并确认操作授权",()=>request("authorize_step",{execution_action_id:execution!.actionId,record_revision:1}),!enabled("authorize_step")));
    else if(!execution.executed)section.append(action("执行已确认的操作",()=>request("execute_step",{execution_action_id:execution!.actionId,record_revision:1}),!enabled("execute_step")));
    const attempt=step["current_attempt_id"];
    if(!this.needsRefresh && typeof attempt==="string" && attempt && typeof run["snapshot_commit_id"]==="string"){
      section.append(action("核验这次执行结果",()=>request("verify_pending",{run_id:run["run_id"],step_id:id,attempt_id:attempt,base_snapshot_commit_id:run["snapshot_commit_id"]},`verify-${execution!.intent}-${String(run["snapshot_commit_id"]).slice(-32)}`),!enabled("verify_pending")));
    }
    section.append(action("刷新步骤与结果",refresh,!enabled("read_view")));
    if(this.note){const status=text("p",this.note);status.setAttribute("role","status");section.append(status);}
    return section;
  }
}

export function friendly(value:unknown):string {
  const labels:Record<string,string>={passed:"通过",failed:"失败",incomplete:"未完成",not_applicable:"不适用",full:"完整验证",quick:"快速检查",on_demand:"所选检查",planned:"按计划执行",stepwise:"逐步执行",running:"运行中",paused:"已暂停",completed:"已完成",pending:"待处理",blocked:"受阻",cancelled:"已取消",ready:"准备就绪",verified:"已核验",unverified:"待核验"};
  return value==null?"待核实":labels[String(value)]??String(value);
}
