# 任务：桌面端 Windows 安装包实测

- 状态：待领取
- 负责执行：人类 + Cursor
- 分支：`fix/win-installer`
- 涉及目录：`desktop/`、`engine/`、`.github/workflows/release.yml`

## 背景

安装包里要同时带上 Electron 前端和用 PyInstaller 打包的引擎，需要在一台干净的 Windows 上验证。

## 要做什么

1. 打一个 tag（如 `v0.1.0-rc.1`），触发 release 工作流。
2. 在没有装 Python 的电脑上安装，确认引擎能自动启动。
3. 走一遍新手引导：填 Key、选工作区、上传一份 PDF、提问。

## 验收标准

- [ ] 安装、启动、提问全流程通过
- [ ] 发现的问题都记录为新的任务单
