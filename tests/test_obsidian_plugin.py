"""obsidian 插件（KiraAI 版）自包含验证脚本。

不依赖 pytest：python test_obsidian_plugin.py 直接跑。
- 用 stub 的 core.plugin 装载 main.py（复刻 KiraAI 插件加载路径：
  plugins.<文件夹名> 包 + spec_from_file_location，验证相对导入可用）；
- 工具层用 FakeClient 注入 canned 响应；
- 客户端层用 httpx.MockTransport 走真实请求/编码/解析路径。

移植自 nori-core tests/unit/plugins/nori_plugin_obsidian/test_obsidian_plugin.py。
"""

from __future__ import annotations

import asyncio
import importlib.util
import logging
import sys
import types
from pathlib import Path
from typing import Any

import httpx

PLUGIN_DIR = Path(__file__).resolve().parent.parent


# ---------------------------------------------------------------- 装载设施


def _install_core_stub() -> None:
    """stub core.plugin（BasePlugin/register/logger），避免拉起整个 KiraAI。"""
    if "core.plugin" in sys.modules:
        return
    core = types.ModuleType("core")
    core_plugin = types.ModuleType("core.plugin")

    class BasePlugin:
        def __init__(self, ctx, cfg):
            self.ctx = ctx
            self.plugin_cfg = cfg or {}

    class _Register:
        @staticmethod
        def tool(name: str, description: str, params: dict):
            def deco(func):
                func._kira_tool = {
                    "name": name,
                    "description": description,
                    "parameters": params,
                }
                return func

            return deco

    core_plugin.BasePlugin = BasePlugin
    core_plugin.register = _Register()
    core_plugin.logger = logging.getLogger("kira.obsidian.test")
    core.plugin = core_plugin
    sys.modules["core"] = core
    sys.modules["core.plugin"] = core_plugin


def _load_plugin_module():
    """按 KiraAI 加载器的方式（plugins.<entry> 包 + spec exec）装载 main.py。"""
    _install_core_stub()
    pkg = types.ModuleType("plugins")
    pkg.__path__ = [str(PLUGIN_DIR.parent)]
    sys.modules["plugins"] = pkg
    sub = types.ModuleType("plugins.kira-ai-plugin-obsidian")
    sub.__path__ = [str(PLUGIN_DIR)]
    sys.modules["plugins.kira-ai-plugin-obsidian"] = sub
    spec = importlib.util.spec_from_file_location(
        "plugins.kira-ai-plugin-obsidian.main", PLUGIN_DIR / "main.py"
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["plugins.kira-ai-plugin-obsidian.main"] = mod
    spec.loader.exec_module(mod)
    return mod


class FakeClient:
    """记录调用并返回预置结果的假 ObsidianClient。"""

    def __init__(self, result: Any = None):
        self.result = result
        self.calls: list[dict] = []

    async def request(self, method, path, *, body=None, content_type=None,
                      accept=None, extra_headers=None):
        self.calls.append({
            "method": method,
            "path": path,
            "body": body,
            "content_type": content_type,
            "accept": accept,
            "extra_headers": extra_headers,
        })
        return self.result


def _make_plugin(cfg: dict):
    mod = _load_plugin_module()
    inst = mod.ObsidianPlugin(None, cfg)
    return inst


async def _init(inst):
    await inst.initialize()
    return inst


# ---------------------------------------------------------------- 注册面


async def test_registers_seven_tools():
    mod = _load_plugin_module()
    cls = mod.ObsidianPlugin
    tools = {
        attr._kira_tool["name"]: attr
        for attr in vars(cls).values()
        if callable(attr) and hasattr(attr, "_kira_tool")
    }
    expected = {
        "note_list", "note_read", "note_write", "note_append",
        "note_patch", "note_delete", "note_search",
    }
    assert set(tools) == expected, f"工具名不符: {sorted(tools)}"
    for name, attr in tools.items():
        meta = attr._kira_tool
        assert meta["description"], f"{name} 缺 description"
        params = meta["parameters"]
        assert params.get("type") == "object", f"{name} params 非 object schema"
    # 必填参数与 nori 版定义对齐
    required = {n: tools[n]._kira_tool["parameters"].get("required", []) for n in tools}
    assert required["note_read"] == ["path"]
    assert required["note_write"] == ["path", "content"]
    assert required["note_append"] == ["path", "content"]
    assert sorted(required["note_patch"]) == ["content", "operation", "path", "target", "target_type"]
    assert required["note_delete"] == ["path"]
    assert required["note_search"] == ["query"]
    assert "path" not in required["note_list"]


async def test_unconfigured_degrades():
    inst = await _init(_make_plugin({}))
    assert inst._client is None
    result = await inst.note_read(None, path="a.md")
    assert "未配置" in result


async def test_bad_optional_fields_fall_back_to_defaults():
    # 可选配置字段坏值只落回该字段默认值，不再拖垮整个插件
    inst = await _init(_make_plugin(
        {"base_url": "http://x", "timeout_seconds": "not-a-number",
         "max_results": None, "summary_max_length": "abc", "max_output_chars": ""}
    ))
    assert inst._client is not None
    assert inst._client._timeout == 15.0
    assert inst._max_results == 8
    assert inst._summary_len == 150
    assert inst._max_output == 8000


async def test_initialize_never_raises_on_garbage_config():
    # 外层兜底仍生效：plugin_cfg 非 dict（无 .get）也不许炸出异常
    inst = await _init(_make_plugin(123))
    assert inst._client is None


async def test_reinit_supported():
    # 热重载重入：initialize 可重复调用且状态一致
    inst = await _init(_make_plugin({"base_url": "http://127.0.0.1:1"}))
    first = inst._client
    await inst.initialize()
    assert inst._client is not None and inst._client is not first
    await inst.terminate()
    assert inst._client is None


# ---------------------------------------------------------------- note_list


async def test_list_formats_entries():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([
        {"path": "日记/2026-08-24.md", "type": "file"},
        {"path": "日记", "type": "folder"},
    ])
    result = await inst.note_list(None)
    assert "共 2 项" in result
    assert "[笔记] 日记/2026-08-24.md" in result
    assert "[目录] 日记" in result
    assert inst._client.calls[0]["path"] == "/vault/"


async def test_list_with_dir_path():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([])
    await inst.note_list(None, path="01-architecture/")
    assert inst._client.calls[0]["path"] == "/vault/01-architecture/"


async def test_list_pagination():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([f"n{i}.md" for i in range(5)])
    result = await inst.note_list(None, page=2, page_size=2)
    assert "第 2/3 页" in result
    assert "n2.md" in result and "n3.md" in result
    assert "n0.md" not in result and "n4.md" not in result
    assert "还有下一页，page=3" in result


async def test_list_accepts_files_dict():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"files": [{"path": "a.md", "type": "file"}]})
    result = await inst.note_list(None)
    assert "[笔记] a.md" in result


async def test_list_error():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"error": "HTTP 404: not found"})
    result = await inst.note_list(None)
    assert result.startswith("错误：HTTP 404")


async def test_list_non_string_error_key_not_misjudged():
    # 顶层 error 键为非字符串的合法 dict 不再被误判为错误
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"error": ["unexpected"], "files": []})
    result = await inst.note_list(None)
    assert "共 0 项" in result


async def test_extra_kwargs_tolerated():
    # 执行器 **args 原样透传，LLM 多传的未声明参数应静默忽略
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([{"path": "a.md", "type": "file"}])
    result = await inst.note_list(None, bogus_param=1)
    assert "a.md" in result


# ---------------------------------------------------------------- note_read


async def test_read_content():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient("# 标题\n正文")
    result = await inst.note_read(None, path="notes/a.md")
    assert "[notes/a.md |" in result
    assert "# 标题" in result
    call = inst._client.calls[0]
    assert call["method"] == "GET" and call["accept"] is None


async def test_read_metadata_accept_header():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"tags": ["x"]})
    result = await inst.note_read(None, path="a.md", mode="metadata")
    assert inst._client.calls[0]["accept"] == "application/vnd.olrapi.note+json"
    assert '"tags"' in result


async def test_read_structure():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"headings": []})
    result = await inst.note_read(None, path="a.md", mode="structure")
    assert inst._client.calls[0]["accept"] == "application/vnd.olrapi.document-map+json"
    assert "| structure]" in result


async def test_read_missing_path_and_invalid_mode():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient()
    assert "缺少 path" in await inst.note_read(None)
    assert "不支持的 mode" in await inst.note_read(None, path="a.md", mode="raw")


async def test_read_truncated():
    inst = await _init(_make_plugin({"base_url": "http://x", "max_output_chars": 500}))
    inst._client = FakeClient("x" * 100000)
    result = await inst.note_read(None, path="big.md")
    assert len(result) < 700
    assert "输出已截断" in result


# ---------------------------------------------------------- write/append/delete


async def test_write_puts_full_body():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    result = await inst.note_write(None, path="reports/t.md", content="# 报告")
    assert "已写入 reports/t.md" in result
    call = inst._client.calls[0]
    assert call["method"] == "PUT"
    assert call["path"] == "/vault/reports/t.md"
    assert call["body"] == "# 报告"


async def test_append_posts():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    result = await inst.note_append(None, path="log.md", content="- 新条目")
    assert "已追加到 log.md" in result
    assert inst._client.calls[0]["method"] == "POST"


async def test_delete():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    result = await inst.note_delete(None, path="old.md")
    assert "已删除 old.md" in result
    assert inst._client.calls[0]["method"] == "DELETE"


async def test_write_missing_content():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient()
    result = await inst.note_write(None, path="a.md")
    assert "缺少 content" in result


async def test_write_auth_error_hint():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"error": "HTTP 401: Unauthorized"})
    result = await inst.note_write(None, path="a.md", content="x")
    assert "认证失败" in result and "api_key" in result


async def test_connection_error_hint():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"error": "connection: [Errno 111] refused"})
    result = await inst.note_write(None, path="a.md", content="x")
    assert "不可达" in result


# ---------------------------------------------------------------- note_patch


async def test_patch_headers():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    result = await inst.note_patch(
        None,
        path="a.md", operation="append", target_type="heading",
        target="主标题::子标题", content="新增段落",
    )
    assert "已局部修改 a.md" in result
    call = inst._client.calls[0]
    assert call["method"] == "PATCH"
    assert call["content_type"] == "text/markdown"
    headers = call["extra_headers"]
    assert headers["Operation"] == "append"
    assert headers["Target-Type"] == "heading"
    # target 需 URL 编码（含 :: 与中文）
    assert headers["Target"] == "%E4%B8%BB%E6%A0%87%E9%A2%98%3A%3A%E5%AD%90%E6%A0%87%E9%A2%98"
    assert "Create-Target-If-Missing" not in headers
    assert "Target-Scope" not in headers


async def test_patch_create_if_missing_and_scope():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    await inst.note_patch(
        None,
        path="a.md", operation="replace", target_type="frontmatter",
        target="tags", content='["x"]',
        create_if_missing=True, target_scope="marker",
    )
    headers = inst._client.calls[0]["extra_headers"]
    assert headers["Create-Target-If-Missing"] == "true"
    assert headers["Target-Scope"] == "marker"


async def test_patch_invalid_operation_no_request():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    result = await inst.note_patch(
        None, path="a.md", operation="delete", target_type="heading",
        target="t", content="x",
    )
    assert "不支持的操作" in result
    assert inst._client.calls == []


async def test_patch_invalid_target_type():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient()
    result = await inst.note_patch(
        None, path="a.md", operation="append", target_type="section",
        target="t", content="x",
    )
    assert "不支持的目标类型" in result


async def test_patch_invalid_target_scope():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    result = await inst.note_patch(
        None, path="a.md", operation="append", target_type="heading",
        target="t", content="x", target_scope="bogus",
    )
    assert "不支持的 target_scope" in result
    assert inst._client.calls == []


async def test_patch_create_if_missing_string_form():
    # LLM 可能以字符串形式传布尔，"true" 应同样触发建章节头
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient(None)
    await inst.note_patch(
        None, path="a.md", operation="append", target_type="heading",
        target="t", content="x", create_if_missing="true",
    )
    headers = inst._client.calls[0]["extra_headers"]
    assert headers["Create-Target-If-Missing"] == "true"


# ---------------------------------------------------------------- note_search


async def test_search_simple_sorted_and_trimmed():
    inst = await _init(_make_plugin(
        {"base_url": "http://x", "max_results": 1, "summary_max_length": 5}
    ))
    inst._client = FakeClient([
        {"filename": "low.md", "score": 1.2, "matches": [{"context": "低相关"}]},
        {"filename": "high.md", "score": 9.8, "matches": [{"context": "高相关命中"}]},
    ])
    result = await inst.note_search(None, query="设计")
    call = inst._client.calls[0]
    assert call["method"] == "POST"
    assert call["path"].startswith("/search/simple/?query=")
    assert "high.md" in result and "low.md" not in result
    assert "相关度:9.80" in result
    assert "高相关" in result


async def test_search_simple_query_encoded():
    # simple 搜索的中文/空格 query 必须预编码
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([])
    await inst.note_search(None, query="设计 文档")
    assert inst._client.calls[0]["path"] == (
        "/search/simple/?query=%E8%AE%BE%E8%AE%A1%20%E6%96%87%E6%A1%A3"
    )


async def test_search_no_results():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([])
    result = await inst.note_search(None, query="不存在")
    assert "未找到相关笔记" in result


async def test_search_dataview():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient([{"filename": "dv.md", "result": "值"}])
    result = await inst.note_search(None, query="LIST FROM 'x'", search_type="dataview")
    call = inst._client.calls[0]
    assert call["path"] == "/search/"
    assert call["content_type"] == "application/vnd.olrapi.dataview.dql+txt"
    assert "dv.md" in result


async def test_search_unsupported_type():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient()
    result = await inst.note_search(None, query="x", search_type="regex")
    assert "不支持的搜索类型" in result


async def test_search_dict_result_hint():
    inst = await _init(_make_plugin({"base_url": "http://x"}))
    inst._client = FakeClient({"unexpected": 1})
    result = await inst.note_search(None, query="x")
    assert "非列表" in result and "simple" in result


# ---------------------------------------------------------------- 真实 Client


def _real_client(handler) -> Any:
    mod = _load_plugin_module()
    return mod.ObsidianClient(
        "http://127.0.0.1:27123/", "secret-key", transport=httpx.MockTransport(handler)
    )


async def test_client_encode_path_segments():
    mod = _load_plugin_module()
    encoded = mod.ObsidianClient("http://x", "k").encode_path(
        "/vault/01-架构/设计 文档.md"
    )
    assert encoded == "/vault/01-%E6%9E%B6%E6%9E%84/%E8%AE%BE%E8%AE%A1%20%E6%96%87%E6%A1%A3.md"


async def test_client_encode_path_keeps_query():
    mod = _load_plugin_module()
    raw = "/search/simple/?query=%E8%AE%BE%E8%AE%A1%20x"
    assert mod.ObsidianClient("http://x", "k").encode_path(raw) == raw
    encoded = mod.ObsidianClient("http://x", "k").encode_path("/目录/?q=1")
    assert encoded == "/%E7%9B%AE%E5%BD%95/?q=1"


async def test_client_request_sends_auth_and_parses_json():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("Authorization")
        seen["url"] = str(request.url)
        return httpx.Response(
            200, json={"files": []}, headers={"Content-Type": "application/json"}
        )

    client = _real_client(handler)
    result = await client.request("GET", "/vault/目录/")
    assert result == {"files": []}
    assert seen["auth"] == "Bearer secret-key"
    assert "/vault/%E7%9B%AE%E5%BD%95/" in seen["url"]


async def test_client_follows_redirects():
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/old.md"):
            return httpx.Response(302, headers={"Location": "/vault/new.md"})
        return httpx.Response(200, text="moved content")

    client = _real_client(handler)
    result = await client.request("GET", "/vault/old.md")
    assert result == "moved content"


async def test_client_204_and_error():
    client = _real_client(lambda req: httpx.Response(204))
    assert await client.request("DELETE", "/vault/a.md") is None
    client = _real_client(lambda req: httpx.Response(404, text="nope"))
    result = await client.request("GET", "/vault/missing.md")
    assert result == {"error": "HTTP 404: nope"}


async def test_client_connection_error():
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("refused")

    client = _real_client(handler)
    result = await client.request("GET", "/vault/")
    assert isinstance(result, dict) and result["error"].startswith("connection:")


async def test_client_body_content_type():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["ct"] = request.headers.get("Content-Type")
        seen["body"] = request.content.decode("utf-8")
        return httpx.Response(204)

    client = _real_client(handler)
    await client.request("PUT", "/vault/a.md", body="# 内容")
    assert seen["ct"] == "text/markdown; charset=utf-8"
    assert seen["body"] == "# 内容"


# ---------------------------------------------------------------- 入口


async def _main():
    tests = [
        (name, fn) for name, fn in sorted(globals().items())
        if name.startswith("test_") and asyncio.iscoroutinefunction(fn)
    ]
    failed = []
    for name, fn in tests:
        try:
            await fn()
            print(f"PASS {name}")
        except Exception as exc:  # noqa: BLE001
            failed.append(name)
            print(f"FAIL {name}: {type(exc).__name__}: {exc}")
            import traceback
            traceback.print_exc()
    print(f"\n{len(tests) - len(failed)}/{len(tests)} passed")
    if failed:
        sys.exit(1)


if __name__ == "__main__":
    asyncio.run(_main())
