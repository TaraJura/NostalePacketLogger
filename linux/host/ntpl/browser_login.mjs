// Headless-Chrome login for the headless client.
//
// Gameforge's iovation device check only trusts a session created by the
// real website login (which solves the PoW challenge), so this drives the
// gameforge.com sign-in form through the Chrome DevTools protocol, then
// generates the site's own game1.js blackboxes in that page. The writable
// outputs (token, plain blackbox, request blackbox) go to GF_OUT, mode
// 0600; nothing secret is printed.
import { spawn } from "node:child_process";
import { chmodSync, mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

const email = process.env.GF_EMAIL;
const password = process.env.GF_PASSWORD;
const installation = process.env.GF_INSTALLATION;
const gsidBase = process.env.GF_GSID;
const feature = Number(process.env.GF_FEATURE);
const out = process.env.GF_OUT;
const chromePath = process.env.GF_CHROME || "/usr/bin/google-chrome";

if (!email || !password || !installation || !gsidBase || !feature || !out) {
  console.error("browser_login: missing GF_* input");
  process.exit(2);
}
mkdirSync(out, { recursive: true });

let chrome = null;
let finished = false;

function stop(code) {
  if (finished) return;
  finished = true;
  try {
    chrome?.kill("SIGKILL");
  } catch (error) {
    // already gone
  }
  process.exit(code);
}
process.on("SIGTERM", () => stop(1));
setTimeout(() => {
  console.error("browser_login: timed out");
  stop(1);
}, 150000).unref();

function fail(message) {
  console.error(`browser_login: ${String(message).slice(0, 400)}`);
  stop(1);
}

async function main() {
  const profile = join(out, "profile");
  chrome = spawn(
    chromePath,
    [
      "--headless=new",
      "--disable-gpu",
      "--no-first-run",
      "--no-default-browser-check",
      "--disable-dev-shm-usage",
      `--user-data-dir=${profile}`,
      "--remote-debugging-port=0",
      "about:blank",
    ],
    { stdio: ["ignore", "ignore", "pipe"] },
  );

  const wsUrl = await new Promise((resolve, reject) => {
    let buffer = "";
    chrome.stderr.on("data", (chunk) => {
      buffer += chunk.toString();
      const match = buffer.match(/DevTools listening on (ws:\/\/\S+)/);
      if (match) resolve(match[1]);
    });
    chrome.on("exit", (code) => reject(new Error(`chrome exited ${code}`)));
    setTimeout(() => reject(new Error("no devtools url")), 20000);
  });

  const ws = new WebSocket(wsUrl);
  await new Promise((resolve, reject) => {
    ws.onopen = resolve;
    ws.onerror = reject;
  });

  let nextId = 1;
  const pending = new Map();
  const captured = [];
  ws.onmessage = (event) => {
    const message = JSON.parse(event.data);
    if (message.id && pending.has(message.id)) {
      const entry = pending.get(message.id);
      pending.delete(message.id);
      if (message.error) entry.reject(new Error(JSON.stringify(message.error)));
      else entry.resolve(message.result);
      return;
    }
    if (message.method === "Network.responseReceived") {
      const { requestId, response } = message.params;
      if (response.url.includes("authProviders/credentials/sessions")) {
        captured.push({ requestId, status: response.status });
      }
    }
  };

  function send(method, params = {}, sessionId) {
    const id = nextId++;
    return new Promise((resolve, reject) => {
      pending.set(id, { resolve, reject });
      ws.send(JSON.stringify({ id, method, params, ...(sessionId ? { sessionId } : {}) }));
    });
  }

  async function evaluate(expression, sessionId) {
    const result = await send(
      "Runtime.evaluate",
      { expression, awaitPromise: true, returnByValue: true, timeout: 90000 },
      sessionId,
    );
    if (result.exceptionDetails) {
      throw new Error(JSON.stringify(result.exceptionDetails).slice(0, 500));
    }
    return result.result?.value;
  }

  const { targetId } = await send("Target.createTarget", {
    url: "https://gameforge.com/en-US/",
  });
  const { sessionId } = await send("Target.attachToTarget", {
    targetId,
    flatten: true,
  });
  await send("Page.enable", {}, sessionId);
  await send("Network.enable", {}, sessionId);
  await new Promise((resolve) => setTimeout(resolve, 9000));

  await evaluate(
    `(() => {
      const button = [...document.querySelectorAll("button")]
        .find(b => (b.textContent || "").trim() === "Accept Cookies");
      if (button) button.click();
      return true;
    })()`,
    sessionId,
  );
  await new Promise((resolve) => setTimeout(resolve, 1000));
  await evaluate(
    `(() => {
      const button = [...document.querySelectorAll("button")]
        .find(b => (b.textContent || "").trim() === "Sign in");
      if (button) button.click();
      return true;
    })()`,
    sessionId,
  );
  await new Promise((resolve) => setTimeout(resolve, 4000));
  await evaluate(
    `(() => {
      const input = document.querySelector("#QA_SignIn_Email_Input");
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, ${JSON.stringify(email)});
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
      document.querySelector("#QA_SignIn_Next_Button")?.click();
      return true;
    })()`,
    sessionId,
  );
  await new Promise((resolve) => setTimeout(resolve, 5000));
  await evaluate(
    `(() => {
      const input = document.querySelector("#QA_SignIn_Password_Input");
      if (!input) return false;
      const setter = Object.getOwnPropertyDescriptor(HTMLInputElement.prototype, "value").set;
      setter.call(input, ${JSON.stringify(password)});
      input.dispatchEvent(new Event("input", {bubbles: true}));
      input.dispatchEvent(new Event("change", {bubbles: true}));
      document.querySelector("#QA_SignIn_SignIn_Button")?.click();
      return true;
    })()`,
    sessionId,
  );
  await new Promise((resolve) => setTimeout(resolve, 16000));

  let token = "";
  for (const item of captured) {
    if (item.status !== 201) continue;
    try {
      const body = await send(
        "Network.getResponseBody",
        { requestId: item.requestId },
        sessionId,
      );
      const parsed = JSON.parse(body.body);
      if (parsed.token) token = parsed.token;
    } catch (error) {
      // body unavailable; keep looking
    }
  }
  if (!token) {
    const challengeId = await evaluate(
      `(() => {
        const frame = document.querySelector("iframe[data-challenge-id]");
        return frame ? frame.dataset.challengeId : "";
      })()`,
      sessionId,
    );
    fail(
      challengeId
        ? "Gameforge showed a manual captcha on the website; use a token"
        : "the website login returned no token",
    );
  }

  const generate = (extra) =>
    `new Promise((resolve, reject) => {
      const finish = (value) => resolve("tra:" + value);
      const call = () => {
        if (${extra ? "true" : "false"}) {
          game1(finish, ${JSON.stringify(extra)});
        } else {
          game1(finish);
        }
      };
      if (window.game1) { call(); return; }
      const script = document.createElement("script");
      script.src = "https://gameforge.com/tra/game1.js";
      script.onload = call;
      script.onerror = () => reject(new Error("game1.js failed"));
      document.body.appendChild(script);
    })`;

  const plain = await evaluate(generate(null), sessionId);
  const extra = await evaluate(
    generate({
      features: [feature],
      installation: installation,
      session: gsidBase,
    }),
    sessionId,
  );
  if (!plain || !extra) fail("the website did not produce a blackbox");

  const write = (name, value) => {
    const path = join(out, name);
    writeFileSync(path, value, { mode: 0o600 });
    chmodSync(path, 0o600);
  };
  write("token", token);
  write("plain", plain);
  write("extra", extra);
  console.log("browser_login: ok");
  ws.close();
  stop(0);
}

main().catch((error) => fail(error));
