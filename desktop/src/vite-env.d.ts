/// <reference types="vite/client" />

type EngineBootstrap = {
  baseUrl: string;
  token: string | null;
  healthy: boolean;
};

type EnsureResult = {
  ok: boolean;
  token: string | null;
  message: string;
};

interface ImportMetaEnv {
  readonly VITE_KAOYAN_TOKEN?: string;
  readonly VITE_KAOYAN_BASE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

declare global {
  interface Window {
    kaoyan?: {
      getBootstrap: () => Promise<EngineBootstrap>;
      ensureEngine: () => Promise<EnsureResult>;
    };
  }
}

export {};
