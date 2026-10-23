import * as vscode from "vscode";
import {spawn} from "node:child_process";
import {randomUUID} from "node:crypto";
import {join,relative,isAbsolute,basename} from "node:path";
import {readFile} from "node:fs/promises";

type HostSettings={python:string;workspaceRoot:string;projectId:string|null;modelConfigurationFile:string};
export type WizardSession={project_id:string;root:string;source:string;entry:string;reference:string;endpoint:string;busy?:boolean};

export function helper(python:string,request:Record<string,unknown>):Promise<Record<string,unknown>> {
  return new Promise((resolve,reject)=>{
    const child=spawn(python,["-I","-m","aitest.interfaces.tools.onboarding"],{shell:false,windowsHide:true,stdio:["pipe","pipe","pipe"],env:{...process.env,PYTHONUTF8:"1",PYTHONIOENCODING:"utf-8"}});
    const chunks:Buffer[]=[];let bytes=0,settled=false;
    const timer=setTimeout(()=>{child.kill();finish(new Error("准备超时，请查看环境状态后再继续。"));},120000);
    const finish=(error?:Error,result?:Record<string,unknown>)=>{if(settled)return;settled=true;clearTimeout(timer);error?reject(error):resolve(result!);};
    child.on("error",()=>finish(new Error("无法启动测试核心，请检查插件运行环境。")));
    child.stdout.on("data",(chunk:Buffer)=>{bytes+=chunk.length;if(bytes>1024*1024){child.kill();finish(new Error("向导响应超出限制"));}else chunks.push(chunk);});
    child.stderr.resume(); // never expose provider errors or secret-bearing diagnostics
    child.on("close",code=>{try{const value=JSON.parse(Buffer.concat(chunks).toString("utf8"));if(code!==0||value.ok!==true)throw new Error();finish(undefined,value.result);}catch{finish(new Error("准备未完成，请核对目录、Python 3.13 和安全凭据。"));}});
    child.stdin.on("error",()=>finish(new Error("无法发送向导配置")));
    child.stdin.end(JSON.stringify(request));
  });
}

export async function wizardMessage(
  context:vscode.ExtensionContext, raw:Record<string,unknown>, session:WizardSession|undefined,
  settings:HostSettings, send:(value:Record<string,unknown>)=>void,
):Promise<WizardSession|undefined> {
  const requestId=raw.clientRequestId;
  const respond=(result:Record<string,unknown>)=>send({kind:"wizardResult",clientRequestId:requestId,...result});
  try {
    if(raw.kind==="wizardModelLoad"){
      const saved=context.globalState.get<Record<string,unknown>>("modelDefaults");
      let configuration:Record<string,unknown>={};
      if(settings.modelConfigurationFile){try{configuration=JSON.parse(await readFile(settings.modelConfigurationFile,"utf8"));}catch{/* show unconfigured */}}
      respond({modelSettings:saved??{endpoint:configuration.endpoint??"https://api.deepseek.com",reference:configuration.credential_reference??"",model:"deepseek-flash",timeout:60}});return session;
    }
    if(raw.kind==="wizardModelCredential"){
      const key=await vscode.window.showInputBox({title:"保存模型 API 密钥",prompt:"密钥只保存在 Windows 安全存储中。",password:true,ignoreFocusOut:true});
      if(!key){respond({cancelled:true});return session;}
      const reference="model-"+randomUUID();await helper(settings.python,{operation:"credential",reference,value:key});
      respond({modelCredential:reference});return session;
    }
    if(raw.kind==="wizardModelSave"){
      if(typeof raw.model!=="string"||!raw.model.trim()||raw.model.length>128||/[\u0000-\u001f]/.test(raw.model)
        ||!Number.isInteger(raw.timeout)||Number(raw.timeout)<10||Number(raw.timeout)>120)throw new Error("请填写模型名称和 10–120 秒超时。");
      const root=join(context.globalStorageUri.fsPath,"model-configurations",randomUUID());
      await helper(settings.python,{operation:"model",root,endpoint:raw.endpoint,reference:raw.reference});
      const defaults={endpoint:raw.endpoint,reference:raw.reference,model:raw.model.trim(),timeout:raw.timeout};
      await context.globalState.update("modelDefaults",defaults);
      respond({modelSettings:defaults,modelSaved:true});return session;
    }
    if(raw.kind==="wizardChoose"){
      const folders=await vscode.window.showOpenDialog({title:"选择要测试的项目目录",canSelectFiles:false,canSelectFolders:true,canSelectMany:false});
      if(!folders?.[0]){respond({cancelled:true});return session;}
      const files=await vscode.window.showOpenDialog({title:"选择 Python 测试入口",defaultUri:folders[0],canSelectFiles:true,canSelectFolders:false,canSelectMany:false,filters:{"Python 测试入口":["py"]}});
      if(!files?.[0]){respond({cancelled:true});return session;}
      const entry=relative(folders[0].fsPath,files[0].fsPath);
      if(isAbsolute(entry)||entry.startsWith("..")||!entry.endsWith(".py"))throw new Error("测试入口必须在所选项目目录内。");
      const project_id="guided-"+randomUUID();
      let reference="",endpoint="https://api.deepseek.com";
      if(settings.modelConfigurationFile){try{const saved=JSON.parse(await readFile(settings.modelConfigurationFile,"utf8"));if(typeof saved.credential_reference==="string")reference=saved.credential_reference;if(typeof saved.endpoint==="string")endpoint=saved.endpoint;}catch{/* unconfigured remains explicit */}}
      const defaults=context.globalState.get<Record<string,unknown>>("modelDefaults");
      if(typeof defaults?.reference==="string")reference=defaults.reference;
      if(typeof defaults?.endpoint==="string")endpoint=defaults.endpoint;
      session={project_id,root:join(context.globalStorageUri.fsPath,"projects",project_id),source:folders[0].fsPath,entry:entry.replaceAll("\\","/"),reference,endpoint};
      respond({session:{...session,name:basename(session.source),model:defaults?.model,timeout:defaults?.timeout}});return session;
    }
    if(!session)throw new Error("请先选择项目目录。");
    if(session.busy)throw new Error("上一步仍在处理，请稍候。");
    session.busy=true;
    if(raw.kind==="wizardCredential"){
      const key=await vscode.window.showInputBox({title:"保存模型 API 密钥",prompt:"仅写入 Windows 凭据管理器，不进入测试记录或普通配置。",password:true,ignoreFocusOut:true});
      if(!key){respond({cancelled:true});return session;}
      const reference=session.project_id+"-model";
      await helper(settings.python,{operation:"credential",reference,value:key});session.reference=reference;
      respond({credential:true,reference});
    }else if(raw.kind==="wizardSetup"){
      if(raw.dataConfirmed!==true)throw new Error("请先确认测试数据隔离与复位方式。");
      const result=await helper(settings.python,{operation:"setup",...session});
      let modelFile="";
      if(raw.useModel===true){
        const model=await helper(settings.python,{operation:"model",root:session.root,endpoint:raw.endpoint,reference:session.reference});
        modelFile=String(model.model_file);session.endpoint=String(raw.endpoint);
      }
      const configuration=vscode.workspace.getConfiguration("aitest");
      for(const [key,value] of Object.entries({workspaceRoot:result.workspace,projectId:session.project_id,runId:"",pythonCarriersFile:result.carriers_file,businessQueriesFile:"",modelConfigurationFile:modelFile}))await configuration.update(key,value,vscode.ConfigurationTarget.Global);
      respond({setup:result,session});
    }else if(raw.kind==="wizardCommands"){
      if(settings.projectId!==session.project_id)throw new Error("当前项目已变化，请回到原项目再继续。");
      const form=raw.form;if(!form||typeof form!=="object"||Array.isArray(form))throw new Error("向导表单无效。");
      const result=await helper(settings.python,{operation:"commands",form:{...form,project_id:session.project_id,source:session.source,entry:session.entry},stage:raw.stage,receipts:raw.receipts});
      respond(result);
    }else throw new Error("向导操作不受支持，请返回当前步骤。");
  }catch(error){respond({error:error instanceof Error?error.message:"向导操作未完成"});}
  finally{if(session)session.busy=false;}
  return session;
}
