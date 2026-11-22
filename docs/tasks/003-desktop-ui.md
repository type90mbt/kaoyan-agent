# 任务：桌面端三栏界面 MVP

- 状态：待审查
- 负责执行：Cursor
- 分支：`feat/desktop-ui`
- 涉及目录：`desktop/`、`docs/tasks/`、`docs/learn/`、`.github/workflows/ci.yml`（仅让缺失子项目不阻塞 CI）

## 背景

引擎已能问答，但还没有窗口。需要一个类似 Cursor 的三栏桌面端，方便日常上课使用。

## 要做什么

1. 用 Electron + React + Vite + Tailwind 搭建 `desktop/`。
2. 左栏：学科/课程/课时树；支持新建学科与课程；选中课程后可上传课件。
3. 中栏：课件页预览或纯文本页；无课件时给出引导。
4. 右栏：流式 AI 对话；绑定当前课程会话；支持本节课总结。
5. Electron 主进程在窗口打开时探测/拉起本地引擎，并把本机令牌注入渲染进程。
6. `npm run typecheck` 与 `npm run build:renderer` 通过。

## 不要做什么

- 不要做安装包打包（任务 006）。
- 不要实现完整设置页的所有模型供应商（先读引擎已有设置即可）。
- 不要改 `engine/` 业务逻辑。

## 验收标准

- [ ] `cd desktop && npm install && npm run typecheck && npm run build:renderer` 通过
- [ ] 能连接本机引擎并完成一次流式对话
- [ ] `docs/learn/` 有桌面端相关说明

## 给学习者的说明

桌面端只是"遥控器"：真正算题、读课件、调模型的是 Python 引擎。Electron 负责开窗口和启动引擎；中间那一层 React 负责画界面和收 SSE 流。
