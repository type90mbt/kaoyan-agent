"""引擎入口：`kaoyan-engine` 或 `python -m kaoyan_engine.main`。"""

from __future__ import annotations

import os
import sys

import uvicorn

from .app import build_state, create_app


def run() -> None:
    host = os.environ.get("KAOYAN_HOST", "127.0.0.1")
    port = int(os.environ.get("KAOYAN_PORT", "8765"))
    state = build_state()
    app = create_app(state)
    print(f"KAOYAN_ENGINE_READY http://{host}:{port}", flush=True)
    print(f"本机令牌文件：{state.store.home / 'local_token'}", file=sys.stderr, flush=True)
    uvicorn.run(app, host=host, port=port, log_level=os.environ.get("KAOYAN_LOG", "warning"))


if __name__ == "__main__":
    run()
