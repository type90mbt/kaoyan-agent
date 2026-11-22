# 贡献指南

欢迎贡献代码、skill、学科包和文档。

## 最简单的贡献：写一个 skill

1. 在 `.agents/skills/` 下新建一个文件夹，名字只能用小写字母、数字和连字符，例如 `history-kaoyan-teach`。
2. 在里面写 `SKILL.md`：

```markdown
---
name: history-kaoyan-teach
description: 讲解历史学考研 313 专业课。用户学习历史学专业课时使用。
metadata:
  subject: history-313
  kind: teach
---

# 历史学 313 讲解

1. ...
```

3. 运行校验：`cd engine && uv run kaoyan-skill-lint ../.agents/skills`
4. 开 PR。

这样写出来的 skill 同时兼容 DeepSeek Harness（DSH），DSH 用户把文件夹复制到 `~/.agents/skills/` 就能用。

## 贡献代码

请先阅读 [AGENTS.md](AGENTS.md)，按里面的流程新建分支、跑检查、开 PR。

## 仓库设置建议（维护者）

- 在 GitHub 仓库 Settings → Branches 中为 `main` 添加保护规则：
  - Require a pull request before merging
  - Require status checks to pass（勾选 CI 中的各个 job）
  - Do not allow bypassing the above settings
