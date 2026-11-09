import {action,newIntent,object,text} from "./shell";

/** Public configuration draft only. Secret entry belongs to the native host. */
export class ModelSettings {
  private request:string|null=null;
  private loaded=false;
  private endpoint="https://api.deepseek.com";
  private model="deepseek-flash";
  private timeout="60";
  private reference="";
  private note="";
  private provider="deepseek";
  private customEndpoint="";
  private errors:Record<string,string>={};
  constructor(private send:(message:unknown)=>void,private render:()=>void){}
  private dispatch(kind:string,extra:Record<string,unknown>={}):void {
    this.request="model-settings-"+newIntent();this.send({kind,clientRequestId:this.request,...extra});
  }
  open():void {if(!this.loaded&&!this.request)this.dispatch("wizardModelLoad");}
  receive(data:Record<string,unknown>):boolean {
    if(!data||data.kind!=="wizardResult"||!this.request||data.clientRequestId!==this.request)return false;
    this.request=null;
    if(data.error)this.note=String(data.error);
    else if(data.cancelled)this.note="已取消，配置未改变。";
    else {
      const settings=object(data.modelSettings);
      if(settings){this.loaded=true;this.endpoint=String(settings.endpoint??"");this.model=String(settings.model??"");this.timeout=String(settings.timeout??60);this.reference=String(settings.reference??"");this.provider=this.endpoint==="https://api.deepseek.com"?"deepseek":"compatible";if(this.provider==="compatible")this.customEndpoint=this.endpoint;}
      if(typeof data.modelCredential==="string"){this.reference=data.modelCredential;this.note="密钥已保存在系统安全存储，请保存配置。";}
      if(data.modelSaved)this.note="默认配置已保存，将用于新建测试。已有运行仍使用原配置；尚未检查模型连通性。";
    }
    this.render();return true;
  }
  status():string {return this.loaded ? this.reference?"已选择安全凭据":"尚未配置凭据":"待配置核对";}
  view(enabled:boolean):HTMLElement {
    const section=document.createElement("section");section.className="form-part";
    section.append(text("p","用于准备测试与分析证据。保存为新建测试的默认配置。"));
    const disabled=!enabled||!!this.request;
    const field=(label:string,key:string,value:string,set:(value:string)=>void)=>{const row=document.createElement("label");row.append(text("span",label));const input=document.createElement("input");input.value=value;input.disabled=disabled;input.dataset.field=key;input.setAttribute("aria-label",label);input.oninput=()=>{set(input.value);delete this.errors[key];input.removeAttribute("aria-invalid");row.querySelector(".field-error")?.remove();};row.append(input);if(this.errors[key]){input.setAttribute("aria-invalid","true");input.setAttribute("aria-describedby",`model-error-${key}`);const error=text("span",this.errors[key]!,"field-error");error.id=`model-error-${key}`;row.append(error);}return row;};
    const provider=document.createElement("label");provider.append(text("span","供应方"));const choice=document.createElement("select");choice.disabled=disabled;
    for(const [value,label] of [["deepseek","DeepSeek"],["compatible","OpenAI 兼容接口"]]){const option=document.createElement("option");option.value=value!;option.textContent=label!;choice.append(option);}
    choice.value=this.provider;
    choice.onchange=()=>{if(this.provider==="compatible")this.customEndpoint=this.endpoint;this.provider=choice.value;this.endpoint=this.provider==="deepseek"?"https://api.deepseek.com":this.customEndpoint;delete this.errors.endpoint;this.render();};provider.append(choice);
    section.append(provider,field("API 地址","endpoint",this.endpoint,value=>this.endpoint=value),field("模型","model",this.model,value=>this.model=value));
    const credential=text("p",this.reference?"API 密钥引用：已配置，正文不会显示在面板中。":"API 密钥引用：尚未配置。");
    const manage=action("管理安全凭据",()=>{this.dispatch("wizardModelCredential");this.render();},disabled);manage.className="link-button";
    section.append(credential,manage,field("请求超时（秒）","timeout",this.timeout,value=>this.timeout=value));
    const save=action("保存默认配置",()=>{
      this.errors={};
      try{const url=new URL(this.endpoint);if(url.protocol!=="https:"||url.username||url.password||url.search||url.hash)throw new Error();}catch{this.errors.endpoint="填写完整的 HTTPS 地址，不要附带账号、密码或查询参数。";}
      if(!this.model.trim()||this.model.length>128||/[\u0000-\u001f]/.test(this.model))this.errors.model="填写供应方提供的模型名称。";
      if(!Number.isInteger(Number(this.timeout))||Number(this.timeout)<10||Number(this.timeout)>120)this.errors.timeout="填写 10–120 之间的整数秒。";
      if(Object.keys(this.errors).length){this.note="请修正标出的配置项，已填写内容会保留。";this.render();return;}
      if(!this.reference){this.note="请先配置安全凭据。";this.render();return;}
      this.dispatch("wizardModelSave",{endpoint:this.endpoint,model:this.model,reference:this.reference,timeout:Number(this.timeout)});this.render();
    },disabled);save.className="primary";section.append(save);
    section.append(text("h3","允许发送的材料"),text("p","新建测试默认只发送项目名称和测试目标，不发送源码。首次模型请求前，会单独展示接收方和材料范围供你确认。"),text("p","连接状态：尚未检查。保存配置会检查格式与安全凭据；实际模型请求的结果会在测试准备中显示。"));
    if(this.note){const status=text("p",this.note,"notice");status.setAttribute("role","status");section.append(status);}return section;
  }
}
