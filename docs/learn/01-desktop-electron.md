# 01 桌面端：为什么是 Electron + React

## 一句话

窗口用 Electron 开，界面用 React 画，真正干活的还是本机 Python 引擎。

## 三块怎么分工

| 进程 | 技术 | 干什么 |
| --- | --- | --- |
| 主进程 | Electron（Node） | 开窗口、找/启动 `kaoyan-engine`、读 `~/.kaoyan/local_token` |
| 预加载 | `preload.ts` | 用 `contextBridge` 把安全的 API 暴露给页面，避免页面直接碰 Node |
| 渲染进程 | React + Vite | 三栏 UI、调 HTTP/SSE、显示课件和对话 |

## 为什么不用纯网页

纯网页打不开本地文件、也不能稳定拉起后台引擎。做成桌面应用后，用户双击就能用，引擎跟着窗口一起启动。

## 流式对话（SSE）是什么

引擎的 `/api/sessions/{id}/chat` 不是一次返回整段答案，而是像打字一样不断推送 `data: {...}` 事件。前端把 `delta` 拼到气泡里，用户就能边等边看。

## 安全点

页面拿不到完整的文件系统权限；需要操作电脑时，仍走引擎的权限层和审批弹窗（后续任务再接到 UI）。
