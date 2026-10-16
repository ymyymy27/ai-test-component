import {test,expect} from "@playwright/test";
import {resolve} from "node:path";
import {postHostMessage} from "./host-outcome";

test("model defaults use correlated host replies and never put a secret field in the panel",async({page})=>{
  await page.setViewportSize({width:280,height:800});
  await page.setContent('<body style="margin:0"><ai-test-panel></ai-test-panel></body>');
  await page.evaluate(()=>{const host=window as unknown as {__sent:unknown[];acquireVsCodeApi:unknown};host.__sent=[];host.acquireVsCodeApi=()=>({postMessage:(message:unknown)=>host.__sent.push(message)});});
  await page.addScriptTag({path:resolve("dist/index.js")});
  await postHostMessage(page,{kind:"outcome",action:"doctor",status:"ok",response:{result:{supported_actions:[]}}});
  await page.getByRole("button",{name:"设置",exact:true}).click();
  const sent=()=>page.evaluate(()=>(window as unknown as {__sent:Record<string,unknown>[]}).__sent.at(-1)!);
  const loading=await sent();expect(loading.kind).toBe("wizardModelLoad");
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:"wrong",modelSettings:{endpoint:"https://foreign.example",model:"foreign"}});
  await expect(page.getByRole("textbox",{name:"API 地址",exact:true})).not.toHaveValue("https://foreign.example");
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:loading.clientRequestId,modelSettings:{endpoint:"https://api.deepseek.com",model:"sample-model",timeout:60,reference:"safe-ref"}});
  await expect(page.getByRole("textbox",{name:"模型",exact:true})).toHaveValue("sample-model");
  await expect(page.locator('input[type="password"]')).toHaveCount(0);
  await page.getByRole("textbox",{name:"模型",exact:true}).fill("new-model");
  await page.getByRole("button",{name:"保存默认配置",exact:true}).click();
  const save=await sent();expect(save).toMatchObject({kind:"wizardModelSave",reference:"safe-ref",model:"new-model",timeout:60});
  expect(save).not.toHaveProperty("value");
  await postHostMessage(page,{kind:"wizardResult",clientRequestId:save.clientRequestId,modelSaved:true});
  await expect(page.getByText(/默认配置已保存/)).toContainText("尚未检查模型连通性");
  expect(await page.evaluate(()=>document.documentElement.scrollWidth<=innerWidth)).toBe(true);
});
