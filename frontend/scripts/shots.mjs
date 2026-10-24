// Скриншоты страниц на 1440 и 360 через headless Chrome или Edge, без лишних зависимостей.
// npm run shots -- /applications/1044?as=sokolov [ещё маршруты…]
// Нужен запущенный dev-сервер (npm run dev) с моками. ?as=<логин> входит под тестовой учёткой.
import { execFileSync } from "node:child_process";
import { existsSync, mkdirSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const BASE = process.env.SHOTS_BASE || "http://localhost:5173";
const WAIT_MS = Number(process.env.SHOTS_WAIT || 4000);
const candidates = [
  process.env.CHROME_PATH,
  "C:/Program Files/Google/Chrome/Application/chrome.exe",
  "C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
  "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
  "/usr/bin/google-chrome",
  "/usr/bin/chromium",
].filter(Boolean);
const browser = candidates.find((p) => existsSync(p));
if (!browser) {
  console.error("Не нашёл Chrome или Edge. Укажите путь в CHROME_PATH");
  process.exit(1);
}

// В Git Bash ведущий «/» превращается в путь Windows, поэтому маршрут можно писать и без него.
const routes = process.argv.slice(2).map((r) => (r.startsWith("/") ? r : "/" + r));
if (!routes.length) {
  console.error("Укажите маршрут: npm run shots -- /?as=bakery");
  process.exit(1);
}

const out = resolve("shots");
mkdirSync(out, { recursive: true });
const sizes = [[1440, 900], [360, 800]];

for (const route of routes) {
  for (const [w, h] of sizes) {
    const name = `${route.replace(/[^a-zA-Z0-9]+/g, "_").replace(/^_|_$/g, "") || "root"}-${w}.png`;
    const file = join(out, name);
    const profile = join(tmpdir(), `ck-shots-${process.pid}-${w}`);
    // Headless Chrome не делает окно уже ~500px, поэтому узкую ширину снимаем через iframe нужного размера.
    // Обёртку отдаёт сам dev-сервер: iframe того же источника живёт в том же процессе и его таймеры идут.
    let target = BASE + route;
    if (w < 500) {
      writeFileSync(join(out, "_frame.html"), `<!doctype html><style>html,body{margin:0}iframe{border:0;width:${w}px;height:${h}px;display:block}</style><iframe src="${route}"></iframe>`);
      target = `${BASE}/shots/_frame.html`;
    }
    execFileSync(browser, [
      "--headless=new",
      "--disable-gpu",
      "--hide-scrollbars",
      "--disable-site-isolation-trials",
      "--disable-features=IsolateOrigins,site-per-process",
      `--user-data-dir=${profile}`,
      `--window-size=${w},${h}`,
      `--virtual-time-budget=${WAIT_MS}`,
      `--screenshot=${file}`,
      target,
    ], { stdio: "ignore" });
    rmSync(profile, { recursive: true, force: true });
    console.log(file);
  }
}
