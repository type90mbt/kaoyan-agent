# Kaoyan Agent 本地引擎

```bash
uv sync
uv run kaoyan-engine            # 默认监听 127.0.0.1:8765
uv run pytest -q
```

环境变量：

| 变量 | 作用 | 默认值 |
| --- | --- | --- |
| `KAOYAN_HOME` | 数据目录（数据库、课件、配置、快照） | `~/.kaoyan` |
| `KAOYAN_HOST` | 监听地址；局域网同步时设为 `0.0.0.0` | `127.0.0.1` |
| `KAOYAN_PORT` | 端口 | `8765` |
| `DEEPSEEK_API_KEY` 等 | 没有存入系统凭据管理器时的备用 Key | 无 |

模块说明见 [docs/learn](../docs/learn)。
