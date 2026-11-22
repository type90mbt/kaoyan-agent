import { app, BrowserWindow, ipcMain } from "electron";
import { spawn, type ChildProcessWithoutNullStreams } from "node:child_process";
import fs from "node:fs";
import http from "node:http";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const ENGINE_HOST = "127.0.0.1";
const ENGINE_PORT = Number(process.env.KAOYAN_PORT || 8765);
const ENGINE_BASE = `http://${ENGINE_HOST}:${ENGINE_PORT}`;

let mainWindow: BrowserWindow | null = null;
let engineProcess: ChildProcessWithoutNullStreams | null = null;
let startedByUs = false;

function kaoyanHome(): string {
  return process.env.KAOYAN_HOME || path.join(os.homedir(), ".kaoyan");
}

function tokenPath(): string {
  return path.join(kaoyanHome(), "local_token");
}

function readToken(): string | null {
  try {
    return fs.readFileSync(tokenPath(), "utf8").trim() || null;
  } catch {
    return null;
  }
}

function httpGet(url: string, timeoutMs = 1500): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const req = http.get(url, { timeout: timeoutMs }, (res) => {
      const chunks: Buffer[] = [];
      res.on("data", (c) => chunks.push(Buffer.from(c)));
      res.on("end", () =>
        resolve({ status: res.statusCode ?? 0, body: Buffer.concat(chunks).toString("utf8") }),
      );
    });
    req.on("timeout", () => {
      req.destroy();
      reject(new Error("timeout"));
    });
    req.on("error", reject);
  });
}

async function engineHealthy(): Promise<boolean> {
  try {
    const res = await httpGet(`${ENGINE_BASE}/api/health`);
    return res.status === 200 && res.body.includes('"ok"');
  } catch {
    return false;
  }
}

function resolveEngineCommand(): { command: string; args: string[]; cwd?: string } {
  const repoRoot = path.resolve(__dirname, "..", "..");
  const engineDir = path.join(repoRoot, "engine");
  const winPython = path.join(engineDir, ".venv", "Scripts", "python.exe");
  const unixPython = path.join(engineDir, ".venv", "bin", "python");
  if (process.platform === "win32" && fs.existsSync(winPython)) {
    return { command: winPython, args: ["-m", "kaoyan_engine.main"], cwd: engineDir };
  }
  if (fs.existsSync(unixPython)) {
    return { command: unixPython, args: ["-m", "kaoyan_engine.main"], cwd: engineDir };
  }
  return { command: "uv", args: ["run", "kaoyan-engine"], cwd: engineDir };
}

async function ensureEngine(): Promise<{ ok: boolean; token: string | null; message: string }> {
  if (await engineHealthy()) {
    return { ok: true, token: readToken(), message: "引擎已在运行" };
  }

  const { command, args, cwd } = resolveEngineCommand();
  try {
    engineProcess = spawn(command, args, {
      cwd,
      env: {
        ...process.env,
        KAOYAN_HOST: ENGINE_HOST,
        KAOYAN_PORT: String(ENGINE_PORT),
      },
      stdio: "pipe",
      windowsHide: true,
    });
    startedByUs = true;
    engineProcess.stdout.on("data", (buf) => {
      if (String(buf).includes("KAOYAN_ENGINE_READY")) {
        // ready marker
      }
    });
    engineProcess.stderr.on("data", () => {
      // keep process alive; errors surface via health check
    });
    engineProcess.on("exit", () => {
      engineProcess = null;
    });
  } catch (err) {
    return {
      ok: false,
      token: null,
      message: `无法启动引擎：${err instanceof Error ? err.message : String(err)}`,
    };
  }

  for (let i = 0; i < 40; i += 1) {
    await new Promise((r) => setTimeout(r, 250));
    if (await engineHealthy()) {
      return { ok: true, token: readToken(), message: "引擎已启动" };
    }
  }
  return { ok: false, token: readToken(), message: "引擎启动超时，请手动在 engine 目录运行 uv run kaoyan-engine" };
}

function createWindow(): void {
  mainWindow = new BrowserWindow({
    width: 1280,
    height: 840,
    minWidth: 960,
    minHeight: 640,
    title: "Kaoyan Agent",
    backgroundColor: "#f4f7f5",
    webPreferences: {
      preload: path.join(__dirname, "preload.js"),
      contextIsolation: true,
      nodeIntegration: false,
      sandbox: true,
    },
  });

  const devUrl = process.env.VITE_DEV_SERVER_URL;
  if (devUrl) {
    void mainWindow.loadURL(devUrl);
  } else {
    void mainWindow.loadFile(path.join(__dirname, "..", "dist", "index.html"));
  }

  mainWindow.on("closed", () => {
    mainWindow = null;
  });
}

ipcMain.handle("engine:ensure", async () => ensureEngine());
ipcMain.handle("engine:getBootstrap", async () => ({
  baseUrl: ENGINE_BASE,
  token: readToken(),
  healthy: await engineHealthy(),
}));

app.whenReady().then(async () => {
  await ensureEngine();
  createWindow();
  app.on("activate", () => {
    if (BrowserWindow.getAllWindows().length === 0) createWindow();
  });
});

app.on("window-all-closed", () => {
  if (process.platform !== "darwin") app.quit();
});

app.on("before-quit", () => {
  if (startedByUs && engineProcess && !engineProcess.killed) {
    engineProcess.kill();
  }
});
