// Drives the real uploader JS from ui.py against a live server.
// Usage: python extracts ui._UP_JS -> app_component.mjs, then: node test_upload_js.mjs
// UPLOAD_MODE=noapi + UPLOAD_BASE=<server without routes> verifies the noapi fallback.
import crypto from "node:crypto"

const BASE = process.env.UPLOAD_BASE || "http://localhost:8599"
const RUN = Date.now().toString(36) // unique id keeps server state from prior runs away
const realFetch = globalThis.fetch
globalThis.fetch = (url, opts) =>
  realFetch(url.startsWith("http") ? url : BASE + url, opts)

const SIZE = 40 * 1024 * 1024 // 40MB -> 16+16+8 chunks
const raw = Buffer.alloc(SIZE)
for (let i = 0; i < SIZE; i++) raw[i] = i % 251
const expectedMd5 = crypto.createHash("md5").update(raw).digest("hex")

const file = new Blob([raw])
file.name = "big.mp4"

function newDom() {
  const el = () => ({ textContent: "", style: {}, disabled: false, files: [] })
  const els = { "#go": el(), "#pick": el(), "#msg": el(), "#fill": el(), "#wrap": el() }
  return { parentElement: { querySelector: (s) => els[s] ?? null }, els }
}

const comp = (await import("./app_component.mjs")).default

async function upload(uploadId) {
  const states = {}
  const { parentElement, els } = newDom()
  comp({
    data: { uploadId },
    parentElement,
    setStateValue: (k, v) => { states[k] = v },
  })
  els["#pick"].files = [file]
  els["#go"].onclick()
  const t0 = Date.now()
  while (!states.status && Date.now() - t0 < 180000) {
    await new Promise((r) => setTimeout(r, 200))
  }
  if (states.status !== "done") throw new Error("status=" + states.status)
  if (states.name !== file.name || states.size !== file.size)
    throw new Error("state mismatch: " + JSON.stringify(states))
  const w = els["#fill"].style.width
  if (w !== "100%") throw new Error("progress width=" + w)
  const st = await (await fetch(BASE + "/api/upload?id=" + uploadId)).json()
  if (st.received !== SIZE) throw new Error("server received=" + st.received)
  console.log(uploadId, "OK", expectedMd5)
}

async function resume() {
  const id = "jsresume" + RUN
  // simulate half-finished upload from an earlier session (same name+size)
  const half = SIZE / 2
  await fetch(BASE + `/api/upload?id=${id}&offset=0&name=big.mp4&size=${SIZE}`, {
    method: "POST",
    body: new Blob([raw.subarray(0, half)]),
  })
  await upload(id) // must skip first half via status resume
  console.log("resume OK")
}

async function expectNoApi() {
  const states = {}
  const { parentElement } = newDom()
  comp({
    data: { uploadId: "noapi" + RUN },
    parentElement,
    setStateValue: (k, v) => { states[k] = v },
  })
  // no click: the mount probe alone must detect the missing API
  const t0 = Date.now()
  while (!states.status && Date.now() - t0 < 30000) {
    await new Promise((r) => setTimeout(r, 200))
  }
  // server without /api/upload serves HTML -> must report noapi, not a JSON error
  if (states.status !== "noapi") throw new Error("status=" + states.status)
  console.log("noapi OK")
}

if (process.env.UPLOAD_MODE === "noapi") {
  await expectNoApi()
} else {
  await upload("jstest" + RUN)
  await resume()
  console.log("JS upload ALL OK")
}
