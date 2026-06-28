"use client";

import { useEffect, useRef, useState } from "react";
import { geoReverse, geoSearch, type GeoPlace } from "@/lib/api";
import { getToken } from "@/lib/auth";

/* eslint-disable @typescript-eslint/no-explicit-any */
declare global {
  interface Window {
    L?: any;
  }
}

const LEAFLET_VER = "1.9.4";
const DEFAULT_CENTER: [number, number] = [-6.2088, 106.8456]; // Jakarta

function loadLeaflet(): Promise<any> {
  return new Promise((resolve, reject) => {
    if (typeof window === "undefined") return reject(new Error("no window"));
    if (window.L) return resolve(window.L);
    if (!document.getElementById("leaflet-css")) {
      const link = document.createElement("link");
      link.id = "leaflet-css";
      link.rel = "stylesheet";
      link.href = `https://unpkg.com/leaflet@${LEAFLET_VER}/dist/leaflet.css`;
      document.head.appendChild(link);
    }
    const existing = document.getElementById("leaflet-js") as HTMLScriptElement | null;
    if (existing) {
      existing.addEventListener("load", () => resolve(window.L));
      existing.addEventListener("error", reject);
      if (window.L) resolve(window.L);
      return;
    }
    const s = document.createElement("script");
    s.id = "leaflet-js";
    s.src = `https://unpkg.com/leaflet@${LEAFLET_VER}/dist/leaflet.js`;
    s.async = true;
    s.onload = () => resolve(window.L);
    s.onerror = reject;
    document.body.appendChild(s);
  });
}

type Props = {
  open: boolean;
  initial?: { lat: number; lon: number } | null;
  onClose: () => void;
  onPick: (place: GeoPlace) => void;
};

export default function MapPicker({ open, initial, onClose, onPick }: Props) {
  const mapEl = useRef<HTMLDivElement>(null);
  const mapRef = useRef<any>(null);
  const markerRef = useRef<any>(null);
  const [place, setPlace] = useState<GeoPlace | null>(null);
  const [loading, setLoading] = useState(false);
  const [q, setQ] = useState("");
  const [results, setResults] = useState<(GeoPlace & { label: string })[]>([]);
  const [searching, setSearching] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  async function reverseAt(lat: number, lon: number) {
    const token = getToken();
    if (!token) return;
    setLoading(true);
    try {
      const p = await geoReverse(token, lat, lon);
      setPlace(p);
    } catch {
      setPlace({ lat, lon, address: "", postal: "", display_name: "" });
    } finally {
      setLoading(false);
    }
  }

  function placeMarker(L: any, lat: number, lon: number) {
    if (!mapRef.current) return;
    const icon = L.icon({
      iconUrl: `https://unpkg.com/leaflet@${LEAFLET_VER}/dist/images/marker-icon.png`,
      iconRetinaUrl: `https://unpkg.com/leaflet@${LEAFLET_VER}/dist/images/marker-icon-2x.png`,
      shadowUrl: `https://unpkg.com/leaflet@${LEAFLET_VER}/dist/images/marker-shadow.png`,
      iconSize: [25, 41],
      iconAnchor: [12, 41],
    });
    if (markerRef.current) markerRef.current.setLatLng([lat, lon]);
    else markerRef.current = L.marker([lat, lon], { icon }).addTo(mapRef.current);
  }

  // Inisialisasi peta saat dibuka.
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    setErr(null);
    loadLeaflet()
      .then((L) => {
        if (cancelled || !mapEl.current) return;
        const center: [number, number] = initial ? [initial.lat, initial.lon] : DEFAULT_CENTER;
        if (!mapRef.current) {
          mapRef.current = L.map(mapEl.current).setView(center, initial ? 16 : 11);
          L.tileLayer("https://{s}.tile.openstreetmap.org/{z}/{x}/{y}.png", {
            attribution: "&copy; OpenStreetMap",
            maxZoom: 19,
          }).addTo(mapRef.current);
          mapRef.current.on("click", (e: any) => {
            placeMarker(L, e.latlng.lat, e.latlng.lng);
            reverseAt(e.latlng.lat, e.latlng.lng);
          });
        } else {
          mapRef.current.setView(center, initial ? 16 : 11);
        }
        if (initial) {
          placeMarker(L, initial.lat, initial.lon);
          reverseAt(initial.lat, initial.lon);
        }
        setTimeout(() => mapRef.current?.invalidateSize(), 150);
      })
      .catch(() => setErr("Gagal memuat peta. Periksa koneksi internet."));
    return () => {
      cancelled = true;
    };
  }, [open]); // eslint-disable-line react-hooks/exhaustive-deps

  // Bersihkan peta saat ditutup agar bisa diinisialisasi ulang dengan benar.
  useEffect(() => {
    if (open) return;
    if (mapRef.current) {
      mapRef.current.remove();
      mapRef.current = null;
      markerRef.current = null;
    }
    setResults([]);
    setQ("");
  }, [open]);

  async function doSearch() {
    const token = getToken();
    if (!token || q.trim().length < 3) return;
    setSearching(true);
    setErr(null);
    try {
      const r = await geoSearch(token, q.trim());
      setResults(r.results);
      if (!r.results.length) setErr("Lokasi tidak ditemukan.");
    } catch {
      setErr("Gagal mencari lokasi.");
    } finally {
      setSearching(false);
    }
  }

  function gotoResult(r: GeoPlace & { label: string }) {
    setResults([]);
    setQ(r.label);
    setPlace(r);
    if (window.L && mapRef.current) {
      mapRef.current.setView([r.lat, r.lon], 16);
      placeMarker(window.L, r.lat, r.lon);
    }
  }

  function myLocation() {
    if (!navigator.geolocation) return setErr("Browser tidak mendukung lokasi.");
    navigator.geolocation.getCurrentPosition(
      (pos) => {
        const { latitude, longitude } = pos.coords;
        if (window.L && mapRef.current) {
          mapRef.current.setView([latitude, longitude], 16);
          placeMarker(window.L, latitude, longitude);
        }
        reverseAt(latitude, longitude);
      },
      () => setErr("Tidak bisa mengambil lokasi (izin ditolak?)."),
    );
  }

  if (!open) return null;

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center p-4" style={{ background: "rgba(0,0,0,0.5)" }}>
      <div className="surface flex w-full max-w-3xl flex-col" style={{ maxHeight: "90vh", overflow: "hidden" }}>
        <div className="flex items-center gap-2 px-4 py-3" style={{ borderBottom: "1px solid var(--ink-150)" }}>
          <span style={{ fontWeight: 600 }}>📍 Pilih Lokasi di Peta</span>
          <span className="grow" />
          <button className="btn btn-ghost btn-sm" onClick={onClose}>✕</button>
        </div>

        <div className="flex flex-wrap items-center gap-2 px-4 py-3">
          <input
            className="input"
            style={{ flex: 1, minWidth: 200 }}
            placeholder="Cari alamat / tempat (mis. Kelapa Gading, Jakarta)"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            onKeyDown={(e) => e.key === "Enter" && doSearch()}
          />
          <button className="btn btn-secondary btn-sm" onClick={doSearch} disabled={searching}>
            {searching ? "Mencari…" : "Cari"}
          </button>
          <button className="btn btn-secondary btn-sm" onClick={myLocation}>📡 Lokasi saya</button>
        </div>

        {results.length > 0 && (
          <div className="mx-4 mb-2 rounded-lg" style={{ border: "1px solid var(--ink-200)", maxHeight: 160, overflow: "auto" }}>
            {results.map((r, i) => (
              <button
                key={i}
                onClick={() => gotoResult(r)}
                className="block w-full px-3 py-2 text-left"
                style={{ fontSize: 12.5, borderBottom: "1px solid var(--ink-100)" }}
              >
                {r.label}
              </button>
            ))}
          </div>
        )}

        <div ref={mapEl} style={{ height: 340, width: "100%", background: "var(--ink-100)" }} />

        <div className="px-4 py-3" style={{ borderTop: "1px solid var(--ink-150)" }}>
          {err && <div className="alert alert-error" style={{ marginBottom: 8 }}>{err}</div>}
          <div style={{ fontSize: 12.5, color: "var(--ink-600)", minHeight: 34 }}>
            {loading ? "Mengambil alamat…" : place ? (
              <>
                <div style={{ color: "var(--ink-800)" }}>{place.display_name || place.address || "—"}</div>
                <div style={{ color: "var(--ink-500)" }}>Kode pos: <b>{place.postal || "(tidak terdeteksi)"}</b></div>
              </>
            ) : (
              "Klik titik di peta, cari alamat, atau pakai lokasi saya."
            )}
          </div>
          <div className="mt-2 flex justify-end gap-2">
            <button className="btn btn-secondary btn-sm" onClick={onClose}>Batal</button>
            <button
              className="btn btn-primary btn-sm"
              disabled={!place}
              onClick={() => place && onPick(place)}
            >
              Gunakan Lokasi Ini
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}
