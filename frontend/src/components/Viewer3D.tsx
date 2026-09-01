"use client";

import { useCallback, useEffect, useRef, useState } from "react";

/**
 * Viewer 3D model EPC (.pvz PTC Creo View) — engine ThingView WASM di /public/viewer3d.
 *
 * Render 100% di BROWSER: server hanya meneruskan byte .pvz (proxy ber-auth,
 * tanpa simpan disk). Engine mengunduh URL model lewat XMLHttpRequest-nya sendiri
 * (di dalam WASM, single-thread — tanpa Worker), sehingga header Authorization
 * disisipkan lewat patch XHR yang HANYA berlaku untuk URL proxy kita.
 *
 * ⚠️ Dirender INLINE, bukan iframe: X-Frame-Options DENY + frame-ancestors 'none'
 * di next.config menolak framing walau same-origin. WASM butuh 'wasm-unsafe-eval'
 * (dan 'unsafe-eval' khusus /part/* untuk embind) di CSP script-src.
 *
 * ⚠️ Byte .wasm (±12,8 MB) DIUNDUH SENDIRI di sini, bukan oleh glue Emscripten:
 * hanya dengan begitu kita punya (a) kemajuan unduhan yang terlihat pemakai,
 * (b) batas waktu berbasis MANDEK bukan jam dinding — koneksi lambat dulu selalu
 * kandas di batas 90 detik dengan pesan menyesatkan "WASM diblokir CSP", dan
 * (c) simpanan di Cache Storage sehingga pembukaan berikutnya nol byte.
 * Glue-nya menerima byte itu lewat `Module.wasmBinary` (didukung: getBinary()).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    ThingView?: any;
    Module?: any;
  }
}

const ENGINE_PATH = "/viewer3d/";
const WASM_URL = `${ENGINE_PATH}libthingview_wasm.wasm`;
/** Ukuran engine saat ini — HANYA untuk menampilkan "x / y MB", bukan gerbang. */
const WASM_PERKIRAAN = 12_861_659;
/** Nama Cache Storage; naikkan versinya bila perlakuan cache berubah. */
const WASM_CACHE = "viewer3d-engine-v1";
/** Menyerah hanya bila TAK ADA byte baru selama ini (bukan batas waktu total). */
const STALL_MS = 45_000;
/** Setelah byte lengkap: kompilasi WASM + init engine (HP/laptop lama bisa lama). */
const INIT_MS = 120_000;

export type EngineProgress = { fase: "unduh" | "siapkan"; byte: number; total: number };

let enginePromise: Promise<any> | null = null;
/** thingview.js sudah ditempel? (jangan 2× — `var Module` akan ditimpa dan init macet) */
let tvScriptPromise: Promise<void> | null = null;
/** libthingview_wasm.js sudah ditempel oleh ThingView.init? (juga tak boleh 2×) */
let glueStarted = false;

const progressListeners = new Set<(p: EngineProgress) => void>();
let progressTerakhir: EngineProgress | null = null;

function lapor(p: EngineProgress) {
  progressTerakhir = p;
  progressListeners.forEach((f) => {
    try {
      f(p);
    } catch {
      /* pendengar mati — abaikan */
    }
  });
}

// ── Unduh engine .wasm sendiri (dengan kemajuan + simpanan) ────────────────
async function dariCache(): Promise<{ buf: ArrayBuffer; etag: string } | null> {
  if (typeof caches === "undefined") return null;
  try {
    const c = await caches.open(WASM_CACHE);
    const r = await c.match(WASM_URL);
    if (!r) return null;
    const etag = r.headers.get("etag") || "";
    const buf = await r.arrayBuffer();
    // Entri kerdil = simpanan rusak/terpotong; lebih baik unduh ulang.
    return buf.byteLength > 1_000_000 ? { buf, etag } : null;
  } catch {
    return null;
  }
}

async function simpanKeCache(buf: ArrayBuffer, etag: string) {
  if (typeof caches === "undefined") return;
  try {
    const c = await caches.open(WASM_CACHE);
    await c.put(
      WASM_URL,
      new Response(buf, { headers: { "Content-Type": "application/wasm", ETag: etag } }),
    );
  } catch {
    /* kuota penuh / mode privat — jalan terus, cuma tanpa simpanan */
  }
}

/** Engine di-scp ulang tiap deploy → ETag berubah. Kalau simpanan sudah basi,
 *  BUANG entri-nya supaya pembukaan BERIKUTNYA mengambil yang baru (yang sekarang
 *  tetap dipakai apa adanya, jadi pemakai tak menunggu). */
function revalidasiDiamDiam(etagCache: string) {
  if (typeof caches === "undefined" || !etagCache) return;
  void fetch(WASM_URL, { method: "HEAD", cache: "no-cache" })
    .then(async (r) => {
      const baru = r.headers.get("etag") || "";
      if (r.ok && baru && baru !== etagCache) {
        const c = await caches.open(WASM_CACHE);
        await c.delete(WASM_URL);
      }
    })
    .catch(() => {
      /* offline / HEAD ditolak — simpanan lama tetap sah */
    });
}

async function unduhWasm(): Promise<ArrayBuffer> {
  const ctrl = new AbortController();
  let terakhir = Date.now();
  const watchdog = window.setInterval(() => {
    if (Date.now() - terakhir > STALL_MS) ctrl.abort();
  }, 5_000);
  try {
    const res = await fetch(WASM_URL, { signal: ctrl.signal, credentials: "same-origin" });
    if (!res.ok) throw new Error(`Engine 3D tak bisa diunduh dari server (HTTP ${res.status}).`);
    const etag = res.headers.get("etag") || "";
    if (!res.body) {
      const buf = await res.arrayBuffer();
      void simpanKeCache(buf, etag);
      return buf;
    }
    // ⚠️ Content-Length tak dipakai: respons ber-gzip & chunked, panjangnya
    // ukuran TERKOMPRES sementara potongan yang kita baca sudah terurai.
    const reader = res.body.getReader();
    const potongan: Uint8Array[] = [];
    let n = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      if (!value) continue;
      potongan.push(value);
      n += value.byteLength;
      terakhir = Date.now();
      lapor({ fase: "unduh", byte: n, total: Math.max(WASM_PERKIRAAN, n) });
    }
    const out = new Uint8Array(n);
    let off = 0;
    for (const p of potongan) {
      out.set(p, off);
      off += p.byteLength;
    }
    const buf = out.buffer as ArrayBuffer;
    void simpanKeCache(buf, etag);
    return buf;
  } catch (e) {
    if (ctrl.signal.aborted) {
      throw new Error(
        `Unduhan engine 3D mandek (tak ada data ${Math.round(STALL_MS / 1000)} detik) — koneksi putus?`,
      );
    }
    throw e instanceof Error ? e : new Error("Unduhan engine 3D gagal.");
  } finally {
    window.clearInterval(watchdog);
  }
}

async function ambilWasm(): Promise<ArrayBuffer> {
  const c = await dariCache();
  if (c) {
    lapor({ fase: "unduh", byte: c.buf.byteLength, total: c.buf.byteLength });
    revalidasiDiamDiam(c.etag);
    return c.buf;
  }
  return unduhWasm();
}

// ── Muat thingview.js + jalankan runtime ───────────────────────────────────
function muatScriptThingView(): Promise<void> {
  if (window.ThingView) return Promise.resolve();
  if (tvScriptPromise) return tvScriptPromise;
  tvScriptPromise = new Promise<void>((resolve, reject) => {
    const s = document.createElement("script");
    s.src = `${ENGINE_PATH}thingview.js`;
    s.async = true;
    s.onload = () =>
      window.ThingView
        ? resolve()
        : reject(new Error("thingview.js termuat tapi tak mendaftarkan engine."));
    s.onerror = () => {
      tvScriptPromise = null;
      reject(new Error("Gagal memuat engine 3D (/viewer3d/thingview.js)."));
    };
    document.head.appendChild(s);
  });
  return tvScriptPromise;
}

function siapkanRuntime(TV: any): Promise<void> {
  return new Promise<void>((resolve, reject) => {
    let selesai = false;
    const bersih = () => {
      selesai = true;
      window.clearTimeout(timer);
      window.removeEventListener("error", onErr, true);
    };
    const beres = () => {
      if (selesai) return;
      bersih();
      resolve();
    };
    const gagal = (pesan: string) => {
      if (selesai) return;
      bersih();
      reject(new Error(pesan));
    };
    // Galat asinkron di dalam engine (mis. EvalError karena CSP) tak pernah sampai
    // ke .catch() promise ini — tanpa pendengar ini kita cuma lihat batas waktu.
    const onErr = (ev: ErrorEvent) => {
      if (ev.filename && ev.filename.includes("/viewer3d/")) {
        gagal(`Engine 3D gagal jalan: ${(ev.message || "galat tak dikenal").slice(0, 200)}`);
      }
    };
    window.addEventListener("error", onErr, true);
    const timer = window.setTimeout(
      () => gagal("Engine 3D tak selesai disiapkan (kompilasi WASM terlalu lama / diblokir CSP)."),
      INIT_MS,
    );
    try {
      if (window.Module) {
        window.Module.onAbort = (w: unknown) =>
          gagal(`Engine 3D berhenti: ${String(w).slice(0, 200)}`);
      }
      // Glue sudah ditempel percobaan sebelumnya: JANGAN init lagi (script kedua
      // akan menimpa `var Module` dan runtime tak pernah selesai) — cukup pasang
      // ulang callback-nya dan tunggu.
      if (glueStarted && !TV.loaded) {
        TV.initCB = beres;
        return;
      }
      glueStarted = true;
      TV.init(ENGINE_PATH, beres);
    } catch (e) {
      gagal(e instanceof Error ? e.message : "Engine 3D gagal inisialisasi.");
    }
  });
}

/** Muat engine sekali per browser (byte .wasm tersimpan di Cache Storage). */
function loadEngine(): Promise<any> {
  if (typeof window === "undefined") return Promise.reject(new Error("Bukan di browser."));
  if (window.ThingView?.loaded) return Promise.resolve(window.ThingView);
  if (enginePromise) return enginePromise;
  enginePromise = (async () => {
    const [wasm] = await Promise.all([ambilWasm(), muatScriptThingView()]);
    const TV = window.ThingView;
    if (!TV) throw new Error("Engine 3D tak terdaftar setelah script dimuat.");
    // Serahkan byte ke glue Emscripten (getBinary → Module["wasmBinary"]) supaya
    // ia tidak mengunduh ulang 12,8 MB lewat instantiateStreaming.
    if (window.Module && !TV.loaded && !window.Module.wasmBinary) {
      window.Module.wasmBinary = wasm;
    }
    lapor({ fase: "siapkan", byte: WASM_PERKIRAAN, total: WASM_PERKIRAAN });
    await siapkanRuntime(TV);
    return TV;
  })().catch((e) => {
    enginePromise = null; // biarkan tombol "Coba lagi" mengulang
    throw e;
  });
  return enginePromise;
}

// ── Patch XHR: Authorization hanya untuk URL proxy file EPC ────────────────
let xhrPatched = false;
let authPrefix = "";
let authToken = "";

function patchXhrAuth(prefix: string, token: string) {
  authPrefix = prefix;
  authToken = token;
  if (xhrPatched) return;
  xhrPatched = true;
  const proto = XMLHttpRequest.prototype as any;
  const origOpen = proto.open;
  const origSend = proto.send;
  proto.open = function (this: any, method: string, url: string | URL, ...rest: any[]) {
    this.__v3dUrl = String(url);
    return origOpen.apply(this, [method, url, ...rest]);
  };
  proto.send = function (this: any, body?: any) {
    const u: string | undefined = this.__v3dUrl;
    if (u && authPrefix && authToken && u.startsWith(authPrefix)) {
      try {
        this.setRequestHeader("Authorization", `Bearer ${authToken}`);
      } catch {
        /* header sudah diset / state salah — biarkan engine yang melapor */
      }
    }
    return origSend.call(this, body);
  };
}

type Props = {
  /** URL proxy .pvz (part3dFileUrl). */
  fileUrl: string;
  token: string;
  height?: number;
};

type Fase = "engine" | "model" | "siap" | "gagal";

function mb(byte: number): string {
  return (byte / 1_048_576).toFixed(1).replace(".", ",");
}

export default function Viewer3D({ fileUrl, token, height = 480 }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const hostId = useRef(`v3d_${Math.random().toString(36).slice(2, 10)}`);
  const sessionRef = useRef<any>(null);
  const [fase, setFase] = useState<Fase>("engine");
  const [err, setErr] = useState("");
  const [percobaan, setPercobaan] = useState(0);
  const [prog, setProg] = useState<EngineProgress | null>(progressTerakhir);

  const cobaLagi = useCallback(() => setPercobaan((n) => n + 1), []);

  useEffect(() => {
    let batal = false;
    // Prefix = endpoint tanpa query, supaya token cuma ikut ke proxy file kita.
    patchXhrAuth(fileUrl.split("?")[0], token);
    setFase("engine");
    setErr("");

    const dengar = (p: EngineProgress) => {
      if (!batal) setProg(p);
    };
    progressListeners.add(dengar);

    loadEngine()
      .then((TV) => {
        if (batal || !hostRef.current) return;
        const M = window.Module;
        const session = TV.CreateSession(hostId.current);
        sessionRef.current = session;
        try { session.ShowProgress(true); } catch { /* opsional */ }
        try { session.SetSelectionFilter(M.SelectionFilter.PART, M.SelectionList.PRIMARYSELECTION); } catch { /* opsional */ }
        try { session.SetAntialiasingMode(M.AntialiasingMode.SS4X); } catch { /* opsional */ }
        // ⚠️ Engine membaca warna sebagai RGBA (0xRRGGBBAA), BUKAN 0xRRGGBB:
        // 0xf5f7fa terbukti tampil CYAN di produksi (R=0x00). Alpha wajib 0xff.
        try { session.SetBackgroundColor(0xf5f7faff); } catch { /* opsional */ }
        setFase("model");
        const model = session.MakeModel();
        model.LoadFromURLWithCallback(fileUrl, true, true, false, (ok: boolean) => {
          if (batal) return;
          if (!ok) {
            setErr("Model 3D gagal dimuat (file EPC tak terambil / token EPC kedaluwarsa?).");
            setFase("gagal");
            return;
          }
          try { session.ZoomView(M.ZoomMode.ZOOM_ALL, 0); } catch { /* opsional */ }
          setFase("siap");
        });
      })
      .catch((e: unknown) => {
        if (batal) return;
        setErr(e instanceof Error ? e.message : "Engine 3D gagal dimuat.");
        setFase("gagal");
      });

    return () => {
      batal = true;
      progressListeners.delete(dengar);
      const s = sessionRef.current;
      sessionRef.current = null;
      if (s && window.ThingView) {
        try { window.ThingView.DeleteSession(s); } catch { /* sudah dihapus */ }
      }
    };
  }, [fileUrl, token, percobaan]);

  // ThingView membuat kanvas SEKALI seukuran wadah dan tak pernah memperbaruinya
  // → tanpa ini, perubahan lebar kolom membuat gambar buram/terpotong.
  useEffect(() => {
    const host = hostRef.current;
    if (!host || typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(() => {
      const c = host.querySelector("canvas");
      if (!c) return;
      const w = host.clientWidth, h = host.clientHeight;
      if (w > 0 && h > 0 && (c.width !== w || c.height !== h)) {
        c.width = w;
        c.height = h;
      }
    });
    ro.observe(host);
    return () => ro.disconnect();
  }, []);

  function muatSemua() {
    const s = sessionRef.current;
    if (!s || !window.Module) return;
    try { s.ZoomView(window.Module.ZoomMode.ZOOM_ALL, 0); } catch { /* opsional */ }
  }

  const teksEngine =
    prog?.fase === "siapkan"
      ? "menyiapkan engine 3D… (kompilasi WASM)"
      : prog
        ? `memuat engine 3D… ${mb(prog.byte)} / ${mb(prog.total)} MB (sekali per browser)`
        : "memuat engine 3D… (sekali per browser, ±13 MB)";

  return (
    <div>
      <div
        id={hostId.current}
        ref={hostRef}
        style={{
          position: "relative",
          width: "100%",
          height,
          borderRadius: 8,
          border: "1px solid var(--ink-200)",
          background: "#f5f7fa",
          overflow: "hidden",
        }}
      >
        {fase !== "siap" && (
          <div
            style={{
              position: "absolute",
              inset: 0,
              display: "flex",
              flexDirection: "column",
              gap: 8,
              alignItems: "center",
              justifyContent: "center",
              padding: 16,
              textAlign: "center",
              fontSize: 12,
              lineHeight: 1.6,
              color: fase === "gagal" ? "var(--danger, #dc2626)" : "var(--ink-500)",
              background: fase === "gagal" ? "rgba(255,255,255,.85)" : "transparent",
              pointerEvents: fase === "gagal" ? "auto" : "none",
            }}
          >
            {fase === "engine" && teksEngine}
            {fase === "model" && "mengunduh model 3D dari EPC…"}
            {fase === "gagal" && (
              <>
                <div>{err}</div>
                <button type="button" className="btn btn-ghost" onClick={cobaLagi} style={{ fontSize: 12 }}>
                  Coba lagi
                </button>
              </>
            )}
          </div>
        )}
      </div>
      <div className="mt-2 flex flex-wrap items-center gap-2" style={{ fontSize: 12, color: "var(--ink-500)" }}>
        <button type="button" className="btn btn-ghost" onClick={muatSemua} disabled={fase !== "siap"} style={{ fontSize: 12 }}>
          ⤢ Muat semua ke layar
        </button>
        <span>seret kiri = putar · scroll = zoom · seret tombol tengah = geser · klik = pilih komponen</span>
      </div>
    </div>
  );
}
