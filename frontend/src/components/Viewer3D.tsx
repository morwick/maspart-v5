"use client";

import { useEffect, useRef, useState } from "react";

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
 * di CSP script-src (sudah ditambah di next.config).
 */

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    ThingView?: any;
    Module?: any;
  }
}

const ENGINE_PATH = "/viewer3d/";
const ENGINE_TIMEOUT_MS = 90_000;

let enginePromise: Promise<any> | null = null;

/** Muat thingview.js + WASM sekali per halaman; mount berikutnya memakai instance yang sama. */
function loadEngine(): Promise<any> {
  if (typeof window === "undefined") return Promise.reject(new Error("Bukan di browser."));
  if (window.ThingView?.loaded) return Promise.resolve(window.ThingView);
  if (enginePromise) return enginePromise;
  enginePromise = new Promise((resolve, reject) => {
    const timer = window.setTimeout(() => {
      enginePromise = null;
      reject(new Error("Engine 3D tak kunjung siap (WASM diblokir CSP / koneksi lambat?)."));
    }, ENGINE_TIMEOUT_MS);
    const s = document.createElement("script");
    s.src = `${ENGINE_PATH}thingview.js`;
    s.async = true;
    s.onload = () => {
      try {
        window.ThingView.init(ENGINE_PATH, () => {
          window.clearTimeout(timer);
          resolve(window.ThingView);
        });
      } catch (e) {
        window.clearTimeout(timer);
        enginePromise = null;
        reject(e instanceof Error ? e : new Error("Engine 3D gagal inisialisasi."));
      }
    };
    s.onerror = () => {
      window.clearTimeout(timer);
      enginePromise = null;
      reject(new Error("Gagal memuat engine 3D (/viewer3d/thingview.js)."));
    };
    document.head.appendChild(s);
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

export default function Viewer3D({ fileUrl, token, height = 480 }: Props) {
  const hostRef = useRef<HTMLDivElement>(null);
  const hostId = useRef(`v3d_${Math.random().toString(36).slice(2, 10)}`);
  const sessionRef = useRef<any>(null);
  const [fase, setFase] = useState<Fase>("engine");
  const [err, setErr] = useState("");

  useEffect(() => {
    let batal = false;
    // Prefix = endpoint tanpa query, supaya token cuma ikut ke proxy file kita.
    patchXhrAuth(fileUrl.split("?")[0], token);
    setFase("engine");
    setErr("");

    loadEngine()
      .then((TV) => {
        if (batal || !hostRef.current) return;
        const M = window.Module;
        const session = TV.CreateSession(hostId.current);
        sessionRef.current = session;
        try { session.ShowProgress(true); } catch { /* opsional */ }
        try { session.SetSelectionFilter(M.SelectionFilter.PART, M.SelectionList.PRIMARYSELECTION); } catch { /* opsional */ }
        try { session.SetAntialiasingMode(M.AntialiasingMode.SS4X); } catch { /* opsional */ }
        try { session.SetBackgroundColor(0xf5f7fa); } catch { /* opsional */ }
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
      const s = sessionRef.current;
      sessionRef.current = null;
      if (s && window.ThingView) {
        try { window.ThingView.DeleteSession(s); } catch { /* sudah dihapus */ }
      }
    };
  }, [fileUrl, token]);

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
            {fase === "engine" && "memuat engine 3D… (sekali per halaman, ±13 MB)"}
            {fase === "model" && "mengunduh model 3D dari EPC…"}
            {fase === "gagal" && err}
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
