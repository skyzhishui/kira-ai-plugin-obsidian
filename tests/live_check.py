"""obsidian 插件部署环境回环验证：部署配置 + 真实 Local REST API 全 CRUD。

用法：在部署机上于本插件目录内运行 `python3 tests/live_check.py`；
配置文件路径默认取 KiraAI 部署位置，可用环境变量 OBSIDIAN_PLUGIN_CONFIG 覆盖。
"""

import asyncio
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from client import ObsidianClient  # noqa: E402  部署的插件模块

_cfg_path = os.environ.get(
    "OBSIDIAN_PLUGIN_CONFIG",
    "/data/KiraAI/data/config/plugins/kira-ai-plugin-obsidian.json",
)
cfg = json.load(open(_cfg_path))
cli = ObsidianClient(cfg["base_url"], cfg["api_key"], timeout=cfg.get("timeout_seconds", 15))
NOTE = f"KiraAI插件验证/scratch-{int(time.time())}.md"


def _step(name):
    print(f"---- STEP {name}", flush=True)


async def main():
    ok = []
    # 0. 清理上次运行遗留的 scratch 笔记（client 契约：传原始路径，内部自行编码）
    _step("cleanup")
    old = await cli.request("GET", "/vault/KiraAI插件验证/")
    if isinstance(old, dict):
        for f in old.get("files", []):
            await cli.request("DELETE", f"/vault/{f}")
    # 1. 根信息
    root = await cli.request("GET", "/")
    assert isinstance(root, dict) and root.get("status") == "OK", root
    ok.append(("root", root["manifest"]["name"] + " " + root["manifest"]["version"]))
    # 2. 列目录
    listing = await cli.request("GET", "/vault/")
    assert isinstance(listing, dict) and "files" in listing, listing
    ok.append(("note_list 路径", f"/vault/ 共 {len(listing['files'])} 项"))
    # 3. 写
    r = await cli.request("PUT", f"/vault/{NOTE}", body="# 验证标题\n\n初始段落。\n\n## 子节\n旧内容。\n")
    assert r is None, r
    # 4. 读
    body = await cli.request("GET", f"/vault/{NOTE}")
    assert isinstance(body, str) and "初始段落" in body, body
    ok.append(("note_write/note_read", f"{len(body)} 字符"))
    # 5. 追加
    r = await cli.request("POST", f"/vault/{NOTE}", body="\n追加行。\n")
    assert r is None, r
    body = await cli.request("GET", f"/vault/{NOTE}")
    assert "追加行" in body
    ok.append(("note_append", "OK"))
    # 6. 局部修改（标题定位 replace，Target 与插件 note_patch 同样 URL 编码）
    # 注意：Local REST API 的 PATCH 返回 200 + 更新后全文（非 204）
    r = await cli.request(
        "PATCH", f"/vault/{NOTE}", body="新内容。",
        content_type="text/markdown",
        extra_headers={
            "Operation": "replace",
            "Target-Type": "heading",
            "Target": "%E9%AA%8C%E8%AF%81%E6%A0%87%E9%A2%98%3A%3A%E5%AD%90%E8%8A%82",
        },
    )
    assert isinstance(r, str) and "新内容。" in r, r
    body = await cli.request("GET", f"/vault/{NOTE}")
    assert "新内容。" in body and "旧内容。" not in body, body
    ok.append(("note_patch", "标题定位 replace 生效"))
    # 7. 搜索
    res = await cli.request("POST", "/search/simple/?query=%E9%AA%8C%E8%AF%81%E6%A0%87%E9%A2%98")
    assert isinstance(res, list), res
    hit = [x for x in res if NOTE in x.get("filename", "")]
    ok.append(("note_search", f"{len(res)} 条命中，含本笔记: {bool(hit)}"))
    # 8. 删除 + 确认 404
    r = await cli.request("DELETE", f"/vault/{NOTE}")
    assert r is None, r
    r = await cli.request("GET", f"/vault/{NOTE}")
    assert isinstance(r, dict) and r.get("error", "").startswith("HTTP 404"), r
    ok.append(("note_delete", "删除后 404 确认"))
    for name, detail in ok:
        print(f"PASS {name}: {detail}")
    print(f"\n{len(ok)}/7 live checks passed")


asyncio.run(main())
