# understanding/ 模板说明

这组模板用于生成 `runs/<taskId>/understanding/` 目录下的中间理解产物。

设计原则：

- 先服务 Agent 建立稳定心智模型，再服务后续文档写作。
- 先写结论，再写支撑，不复制长篇原始证据。
- 每条重要结论都应回指 `evidence.json` 中的证据 id。
- 明确区分 `事实`、`推断` 和 `未知`。
- 所有路径统一使用仓库相对路径。

建议约定：

- Markdown 模板统一使用 YAML front matter。
- JSON 模板统一使用 `meta` + `items` 的顶层结构。
- 后续脚本若要自动填充，可直接复用这些模板文件名。
