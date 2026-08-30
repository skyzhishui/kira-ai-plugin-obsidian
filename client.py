"""Obsidian Local REST API 客户端（httpx 直连，无 SDK 依赖）。

对应 Obsidian 社区插件 "Local REST API"（coddingtonbear）：
- GET    /vault/{path}/   列目录；GET /vault/{path} 读笔记
- PUT    /vault/{path}    全量写（覆盖）；POST /vault/{path} 追加
- PATCH  /vault/{path}    局部修改（Operation/Target-Type/Target 自定义头）
- DELETE /vault/{path}    删除
- POST   /search/simple/  关键词搜索；POST /search/ dataview/jsonlogic

每次请求独立 AsyncClient（笔记工具调用低频，免会话生命周期管理，
插件热重载/卸载无残留连接）。错误不抛异常，统一返回 {"error": ...}
dict，由工具层转为错误文本喂给 LLM。
"""

from __future__ import annotations

import json
import logging
import urllib.parse
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class ObsidianClient:
    """Obsidian Local REST API 客户端（base_url + Bearer api_key）。"""

    def __init__(
        self,
        base_url: str,
        api_key: str,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        """初始化客户端。

        Args:
            base_url: REST API 地址（如 http://127.0.0.1:27123，尾随 / 会剥掉）。
            api_key: Bearer 密钥（空则不带认证头）。
            timeout: 单请求总超时秒数。
            transport: 可选注入的 httpx 传输层（测试用 MockTransport）。
        """
        self._base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._timeout = timeout
        self._transport = transport

    @staticmethod
    def _encode(path: str) -> str:
        """按 '/' 分段逐段 URL 编码（中文、空格等特殊字符安全传输）。"""
        return "/".join(urllib.parse.quote(seg, safe="") for seg in path.split("/"))

    def encode_path(self, path: str) -> str:
        """编码请求路径；含 '?' 时只编码路径部分，query 原样保留。"""
        if not path:
            return ""
        if "?" in path:
            path_part, query = path.split("?", 1)
            return f"{self._encode(path_part)}?{query}"
        return self._encode(path)

    async def request(
        self,
        method: str,
        path: str,
        *,
        body: str | None = None,
        content_type: str | None = None,
        accept: str | None = None,
        extra_headers: dict[str, str] | None = None,
    ) -> Any:
        """发起请求并解析响应。

        Args:
            method: HTTP 方法（GET/PUT/POST/PATCH/DELETE）。
            path: 以 / 开头的请求路径（自动分段 URL 编码）。
            body: 请求体（UTF-8 Markdown 文本）。
            content_type: body 的 Content-Type（缺省 text/markdown; charset=utf-8）。
            accept: Accept 头（metadata/structure 模式指定 olrapi JSON 类型）。
            extra_headers: PATCH 专用自定义头等。

        Returns:
            204 返回 None；JSON 响应解析为 dict/list；其余为响应文本；
            HTTP >=400 或网络异常返回 {"error": 描述} dict（不抛异常）。
        """
        url = f"{self._base_url}{self.encode_path(path)}"
        headers: dict[str, str] = {}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        if body is not None:
            headers["Content-Type"] = content_type or "text/markdown; charset=utf-8"
        if accept:
            headers["Accept"] = accept
        if extra_headers:
            headers.update(extra_headers)
        data = body.encode("utf-8") if body is not None else None
        logger.debug("Obsidian %s %s", method, url)
        try:
            async with httpx.AsyncClient(
                timeout=self._timeout,
                transport=self._transport,
                follow_redirects=True,
            ) as client:
                resp = await client.request(method, url, headers=headers, content=data)
        except httpx.TimeoutException:
            logger.error("Obsidian 请求超时: %s %s", method, url)
            return {"error": f"timeout ({self._timeout:.0f}s)"}
        except httpx.HTTPError as exc:
            logger.error("Obsidian 请求失败: %s %s", method, url)
            return {"error": f"connection: {exc}"}

        if resp.status_code == 204:
            return None
        text = resp.text
        if resp.status_code >= 400:
            detail = text[:500] if text else "无错误详情"
            logger.error("Obsidian HTTP %s: %s", resp.status_code, detail)
            return {"error": f"HTTP {resp.status_code}: {detail}"}
        if "json" in resp.headers.get("Content-Type", ""):
            try:
                return json.loads(text)
            except json.JSONDecodeError:
                return text
        return text
