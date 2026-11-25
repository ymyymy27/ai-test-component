"""跨语言、跨框架通用契约对账与代码图准确率泛化测试集（Hold-out 独立合成项目）。"""

from __future__ import annotations

import json
from pathlib import Path

from aitest.core.code_graph import build_code_graph
from aitest.core.context.contracts import (
    _api_routes,
    _api_routes_with_meta,
    _frontend_api_calls,
    _frontend_assets,
)
from aitest.core.probes import ContractReconciliationProbe


def _write(root: Path, relative: str, content: str) -> None:
    path = root / relative
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def _check_contract(root: Path) -> list[dict[str, object]]:
    routes, meta = _api_routes_with_meta(root, max_routes=2000)
    calls = _frontend_api_calls(root, max_files=2000, max_calls=1000)
    res = ContractReconciliationProbe().run(
        root=root,
        frontend_api_calls=calls,
        api_routes=routes,
        has_dynamic_loader=bool(meta.get("has_dynamic_loader")),
    )
    return list(res.get("findings") or [])


def test_fastapi_fstring_nested_depends_and_pydantic_schema_vs_dict(tmp_path: Path) -> None:
    """FastAPI f-string 嵌套、Depends 括号、空子路径装饰器与闭集 Pydantic 模型 vs 透传 dict。"""
    _write(
        tmp_path,
        "srv/settings.py",
        'ROOT_API = "/api"\nAPI_V2_PATH = f"{ROOT_API}/v2"\n',
    )
    _write(
        tmp_path,
        "srv/schemas.py",
        (
            "from pydantic import BaseModel\n\n"
            "class OrderInput(BaseModel):\n"
            "    sku: str\n"
            "    qty: int\n"
        ),
    )
    _write(
        tmp_path,
        "srv/routes/orders.py",
        (
            "from fastapi import APIRouter, Depends, Request\n"
            "from srv.schemas import OrderInput\n"
            "from srv.settings import API_V2_PATH\n\n"
            "def guard(): return True\n"
            'router = APIRouter(dependencies=[Depends(guard)], prefix=f"{API_V2_PATH}/orders")\n\n'
            '@router.get("")\n'
            "def list_orders():\n    return []\n\n"
            '@router.post("")\n'
            "def create_order(body: OrderInput):\n    return body\n\n"
            '@router.patch("/flex")\n'
            "async def patch_flex(req: Request):\n    return {}\n"
        ),
    )
    _write(
        tmp_path,
        "web/orders.ts",
        (
            "export const load = () => fetch('/api/v2/orders');\n"
            "export const addOk = () => fetch('/api/v2/orders', {\n"
            "  method: 'POST', body: JSON.stringify({ sku: 'A1', qty: 2 })\n"
            "});\n"
            "export const patchOk = () => fetch('/api/v2/orders/flex', {\n"
            "  method: 'PATCH', body: JSON.stringify({ dynamicFlag: true })\n"
            "});\n"
        ),
    )
    assert _check_contract(tmp_path) == []

    # 故意提交 Pydantic 闭集模型未声明字段应精准捕获 error/field_mismatch
    _write(
        tmp_path,
        "web/bad_order.ts",
        (
            "export const addBad = () => fetch('/api/v2/orders', {\n"
            "  method: 'POST', body: JSON.stringify({ sku: 'A1', ghostProp: 9 })\n"
            "});\n"
        ),
    )
    findings = _check_contract(tmp_path)
    assert len(findings) == 1
    assert findings[0]["kind"] == "field_mismatch"
    assert findings[0]["severity"] == "error"
    assert "ghostProp" in str(findings[0]["detail"])


def test_plugin_toml_declarative_routes_across_arbitrary_modules(tmp_path: Path) -> None:
    """通用识别任意目录下的 plugin.toml + api/vN 子包前缀组合。"""
    _write(
        tmp_path,
        "extensions/billing/plugin.toml",
        '[plugin]\nenabled = true\n\n[api.invoices]\nprefix = "/invoices"\n',
    )
    _write(
        tmp_path,
        "extensions/billing/api/v1/finance/invoices.py",
        (
            "from fastapi import APIRouter\n"
            "router = APIRouter()\n"
            '@router.get("/summary")\n'
            "def summary(): return {}\n"
        ),
    )
    _write(
        tmp_path,
        "frontend/billing.ts",
        "export const getInv = () => fetch('/api/v1/finance/invoices/summary');\n",
    )
    assert _check_contract(tmp_path) == []


def test_flask_and_django_nested_routes(tmp_path: Path) -> None:
    """Flask Blueprint 多层挂载与 Django urlpatterns include() 嵌套均应准确匹配。"""
    _write(
        tmp_path,
        "flask_app/users.py",
        (
            "from flask import Blueprint\n"
            "bp = Blueprint('users', __name__, url_prefix='/users')\n"
            "@bp.get('/profile')\n"
            "def profile(): return {}\n"
        ),
    )
    _write(
        tmp_path,
        "flask_app/app.py",
        (
            "from flask import Flask\n"
            "from flask_app.users import bp\n"
            "app = Flask(__name__)\n"
            "app.register_blueprint(bp, url_prefix='/api')\n"
        ),
    )
    _write(
        tmp_path,
        "dj_proj/sub/urls.py",
        (
            "from django.urls import path\n"
            "urlpatterns = [path('<int:item_id>/detail/', lambda r: None)]\n"
        ),
    )
    _write(
        tmp_path,
        "dj_proj/urls.py",
        (
            "from django.urls import include, path\n"
            "urlpatterns = [path('api/catalog/', include('dj_proj.sub.urls'))]\n"
        ),
    )
    _write(
        tmp_path,
        "ui/calls.ts",
        (
            "export const f1 = () => fetch('/api/users/profile');\n"
            "export const f2 = (id: string) => "
            "fetch(`/api/catalog/${id}/detail/`, { method: 'POST' });\n"
        ),
    )
    assert _check_contract(tmp_path) == []


def test_nextjs_zod_early_branch_passthrough_and_pages_router(tmp_path: Path) -> None:
    """Next.js App Router 早退属性并入、透传不误报、Pages Router、且抓出 Zod 越界真故障。"""
    _write(
        tmp_path,
        "src/lib/validators.ts",
        "export const createWidgetSchema = z.object({ title: z.string(), weight: z.number() });\n",
    )
    _write(
        tmp_path,
        "src/app/api/widgets/route.ts",
        (
            "import { createWidgetSchema } from '@/lib/validators';\n"
            "export async function POST(req: Request) {\n"
            "  const rawBody = await req.json();\n"
            "  if (rawBody.action === 'validateOnly') return { ok: true };\n"
            "  const parsed = createWidgetSchema.safeParse(rawBody);\n"
            "  return parsed;\n"
            "}\n"
        ),
    )
    _write(
        tmp_path,
        "src/app/api/passthrough/route.ts",
        (
            "export async function POST(req: Request) {\n"
            "  const payload = await req.json();\n"
            "  return saveDynamic(payload);\n"
            "}\n"
        ),
    )
    _write(
        tmp_path,
        "pages/api/webhooks/[provider].ts",
        "export default function h(req: any, res: any) { if (req.method === 'POST') res.end(); }\n",
    )
    _write(
        tmp_path,
        "src/views/widget.tsx",
        (
            "export const validate = () => fetch('/api/widgets', {\n"
            "  method: 'POST', body: JSON.stringify({ action: 'validateOnly' })\n"
            "});\n"
            "export const create = () => fetch('/api/widgets', {\n"
            "  method: 'POST', body: JSON.stringify({ title: 'box', weight: 3 })\n"
            "});\n"
            "export const flex = () => fetch('/api/passthrough', {\n"
            "  method: 'POST', body: JSON.stringify({ anyKey: 1 })\n"
            "});\n"
            "export const hook = (p: string) => fetch(`/api/webhooks/${p}`, { method: 'POST' });\n"
        ),
    )
    assert _check_contract(tmp_path) == []

    # 真越界字段仍应被揪出
    _write(
        tmp_path,
        "src/views/bad_widget.tsx",
        (
            "export const bad = () => fetch('/api/widgets', {\n"
            "  method: 'POST', body: JSON.stringify({ title: 'x', bogusProp: 1 })\n"
            "});\n"
        ),
    )
    findings = _check_contract(tmp_path)
    assert len(findings) == 1
    assert findings[0]["kind"] == "field_mismatch"
    assert findings[0]["severity"] == "error"
    assert "bogusProp" in str(findings[0]["detail"])


def test_express_use_axios_baseurl_and_delete_data_unwrap(tmp_path: Path) -> None:
    """Express 跨文件 app.use、Axios baseURL 拼接以及 delete({ data: { ids } }) 精准解包。"""
    _write(
        tmp_path,
        "server/routes/records.ts",
        (
            "import express from 'express';\n"
            "const r = express.Router();\n"
            "const DeleteSchema = z.object({ ids: z.array(z.number()) });\n"
            "r.delete('/records', (req, res) => { DeleteSchema.parse(req.body); res.json({}); });\n"
            "export default r;\n"
        ),
    )
    _write(
        tmp_path,
        "server/index.ts",
        "import recordsRouter from './routes/records';\napp.use('/api/v2', recordsRouter);\n",
    )
    _write(
        tmp_path,
        "frontend/api.ts",
        (
            "const apiV2 = axios.create({ baseURL: '/api/v2' });\n"
            "export const delBatch = (ids: number[]) => "
            "apiV2.delete('/records', { data: { ids } });\n"
            "export const delDyn = (payload: any) => apiV2.delete('/records', { data: payload });\n"
        ),
    )
    assert _check_contract(tmp_path) == []


def test_nestjs_go_spring_and_fetch_ternary_and_custom_wrapper(tmp_path: Path) -> None:
    """NestJS/Go/Spring Boot 路由提取，及 Fetch RequestInit 隔离、三元 method 与自定义包装。"""
    _write(
        tmp_path,
        "nest/cats.controller.ts",
        "@Controller('/api/cats')\nexport class CatsCtrl { @Get('/list') list() {} }\n",
    )
    _write(
        tmp_path,
        "go_srv/main.go",
        'grp := r.Group("/api/nodes")\ngrp.POST("/register", h)\ngrp.DELETE("/register", h)\n',
    )
    _write(
        tmp_path,
        "java_srv/AuthCtrl.java",
        (
            '@RequestMapping("/api/session")\n'
            'public class AuthCtrl { @PostMapping("/login") public void login() {} }\n'
        ),
    )
    _write(
        tmp_path,
        "web/app.ts",
        (
            "export const cats = () => fetch('/api/cats/list', {\n"
            "  headers: { Authorization: 'x' }, credentials: 'include'\n"
            "});\n"
            "export const toggleNode = (up: boolean) => fetch('/api/nodes/register', {\n"
            "  method: up ? 'POST' : 'DELETE', body: JSON.stringify({ id: 'n1' })\n"
            "});\n"
            "export const submitAuthForm = (d: any) => completeSession('/api/session/login', d);\n"
        ),
    )
    assert _check_contract(tmp_path) == []


def test_openapi_many_fields_ref_expansion_and_vite_leading_slash_assets(tmp_path: Path) -> None:
    """OpenAPI $ref 展开超越 8 个属性不截断、源码优先降级，及 Vite 子目录前导 / 资源不误报。"""
    props = {f"field_{i}": {"type": "string"} for i in range(12)}
    _write(
        tmp_path,
        "openapi.json",
        json.dumps(
            {
                "openapi": "3.0.0",
                "components": {"schemas": {"WideReq": {"type": "object", "properties": props}}},
                "paths": {
                    "/api/wide": {
                        "post": {
                            "requestBody": {
                                "content": {
                                    "application/json": {
                                        "schema": {"$ref": "#/components/schemas/WideReq"}
                                    }
                                }
                            }
                        }
                    }
                },
            }
        ),
    )
    _write(
        tmp_path,
        "apps/web/index.html",
        (
            '<script type="module" src="/src/main.ts"></script>'
            '<script src="/absent.js"></script>'
        ),
    )
    _write(tmp_path, "apps/web/package.json", '{"name": "sub-web"}')
    _write(tmp_path, "apps/web/src/main.ts", "console.log('ok');\n")
    _write(
        tmp_path,
        "apps/web/call.ts",
        (
            "export const submitWide = () => fetch('/api/wide', {\n"
            "  method: 'POST', body: JSON.stringify({ field_11: 'ok' })\n"
            "});\n"
        ),
    )
    assert _check_contract(tmp_path) == []
    assets = {a["href"]: a["exists_in_repo"] for a in _frontend_assets(tmp_path)}
    assert assets["/src/main.ts"] is True
    assert assets["/absent.js"] is False


def test_external_systems_detection_and_dynamic_loader_severity(tmp_path: Path) -> None:
    """基于清单自动识别外部系统节点，存在动态加载器时缺失路由科学降级为 warning。"""
    _write(tmp_path, "package.json", '{"dependencies": {"ioredis": "^5.0.0", "pg": "^8.0.0"}}')
    _write(
        tmp_path,
        "backend/loader.py",
        (
            "import importlib\n"
            "def load(router, m):\n"
            "    router.include_router(importlib.import_module(m).router)\n"
        ),
    )
    _write(
        tmp_path,
        "backend/base.py",
        (
            "from fastapi import APIRouter\n"
            "r = APIRouter()\n"
            "@r.get('/api/ping')\n"
            "def ping(): return 'ok'\n"
        ),
    )
    _write(tmp_path, "frontend/app.ts", "fetch('/api/plugin/dynamic-ep');\n")

    routes = _api_routes(tmp_path)
    assert any(r["path"] == "/api/ping" for r in routes)
    graph = build_code_graph(tmp_path, {})
    ext_names = {e["name"] for e in graph["external_systems"]}
    assert "Redis" in ext_names
    assert "PostgreSQL" in ext_names
    assert any(
        c["kind"] == "missing_route" and c["severity"] == "warning"
        for c in graph["conflicts"]
    )
