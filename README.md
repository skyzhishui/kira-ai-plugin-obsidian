# kira-ai-plugin-obsidian

**Obsidian 笔记工具** — 赋予 KiraAI 读写 Obsidian 笔记库（vault）的能力。

## 功能

| 工具 | 描述 |
|------|------|
| `note_list` | 列出指定目录下的笔记与文件夹（分页） |
| `note_read` | 读取笔记：content（Markdown 正文，默认）/ metadata（含 frontmatter 的元数据 JSON）/ structure（文档大纲 JSON）三种模式 |
| `note_write` | 创建或全量替换笔记（不存在则创建） |
| `note_append` | 追加内容到笔记末尾（不存在则创建），适合日记、记录类追加 |
| `note_patch` | 局部修改：对标题（`::` 嵌套定位）/ 块引用（^id）/ frontmatter 字段做 append/prepend/replace，无需重写整篇 |
| `note_delete` | 删除笔记（不可逆，LLM 被要求删除前先向对方确认） |
| `note_search` | 搜索笔记：simple 关键词 / dataview DQL / jsonlogic 规则，返回 `文件名\|相关度\|摘要` 列表 |

frontmatter 解析、markdown 结构解析、搜索打分全部由 Obsidian 侧完成，插件本地零解析。工具失败返回错误描述文本（不抛异常），LLM 可据此调整参数重试。

## 配置

依赖 Obsidian 社区插件
[Local REST API](https://github.com/coddingtonbear/obsidian-local-rest-api)（KiraAI 经它与 vault 通信），需先在 Obsidian 中安装并启用。

| 字段 | 说明 | 默认值 |
|------|------|--------|
| `base_url` | Local REST API 服务地址。Obsidian 与 KiraAI 同机填 `http://127.0.0.1:27123`；远端填 Obsidian 所在机器的可达地址 | 空（留空时工具降级为返回配置提示，不影响主程序） |
| `api_key` | Local REST API 生成的密钥 | 空 |
| `timeout_seconds` | 单次 HTTP 请求超时秒数 | 15 |
| `max_results` | `note_search` 结果最多展示条数 | 8 |
| `summary_max_length` | 每条搜索摘要的截断字符数 | 150 |
| `max_output_chars` | 工具输出最大字符数，超出截断并标注 | 8000 |

### 获取 api_key 方法

1. Obsidian → 设置 → 第三方插件，安装并启用 **Local REST API**
2. 插件设置页将认证模式设为 required，复制 API Key
3. 保持 Obsidian 运行，把服务地址与 API Key 填入 KiraAI WebUI 插件配置

## 安装

1. KiraAI WebUI → 插件页 → 从 GitHub 安装，填入 `https://github.com/skyzhishui/kira-ai-plugin-obsidian`
2. 也可以下载本仓库 zip，通过 WebUI 上传安装
3. 安装完成后在插件配置页填入 `base_url` 与 `api_key`

## 依赖

无额外依赖。HTTP 客户端复用 KiraAI 本体自带的 `httpx`（本体依赖已含 `httpx>=0.28.1`）。

## 本地测试

```
python tests/test_obsidian_plugin.py
```

自包含验证脚本（无需 pytest）：复刻 KiraAI 插件加载路径装载 main.py，覆盖七工具注册面、参数校验、输出格式化与 client 的编码、认证、错误契约。`tests/live_check.py` 为部署环境回环验证脚本（对真实 Obsidian 全 CRUD，自建自删临时笔记）。

## 信息

- **插件 ID**: `kira-ai-plugin-obsidian`
- **版本**: 1.0.0
- **作者**: skyzhishui
