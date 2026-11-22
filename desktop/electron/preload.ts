import { contextBridge, ipcRenderer } from "electron";

export type EngineBootstrap = {
  baseUrl: string;
  token: string | null;
  healthy: boolean;
};

export type EnsureResult = {
  ok: boolean;
  token: string | null;
  message: string;
};

contextBridge.exposeInMainWorld("kaoyan", {
  getBootstrap: (): Promise<EngineBootstrap> => ipcRenderer.invoke("engine:getBootstrap"),
  ensureEngine: (): Promise<EnsureResult> => ipcRenderer.invoke("engine:ensure"),
});
