"use client";

import { useEffect, useRef, useState, type ReactNode } from "react";

/**
 * Lightbox gambar layar-penuh dengan ZOOM + GESER (pan).
 * - scroll / roda mouse  → zoom (1×–8×)
 * - seret saat ter-zoom  → geser
 * - klik dua kali        → reset
 * - Esc / klik latar / ✕ → tutup
 *
 * Dipakai bersama oleh foto part (halaman detail) & gambar exploded view
 * (asisten AI). Caller memberi `src` yang SUDAH siap tampil (URL/objectURL).
 */
export default function ImageLightbox({
  src,
  onClose,
  alt = "Gambar",
  caption,
}: {
  src: string;
  onClose: () => void;
  alt?: string;
  caption?: ReactNode;
}) {
  const [zoom, setZoom] = useState(1);
  const [pan, setPan] = useState({ x: 0, y: 0 });
  const [dragging, setDragging] = useState(false);
  const boxRef = useRef<HTMLDivElement>(null);
  const dragRef = useRef<{ x: number; y: number } | null>(null);
  const movedRef = useRef(false);
  const zoomRef = useRef(1);

  // Esc menutup.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => e.key === "Escape" && onClose();
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  // Zoom ≤ 1 → tak ada geseran (gambar terpusat).
  useEffect(() => {
    zoomRef.current = zoom;
    if (zoom <= 1) setPan({ x: 0, y: 0 });
  }, [zoom]);

  // Roda mouse = zoom (listener non-passive agar preventDefault berlaku),
  // gerak mouse = geser saat ter-zoom.
  useEffect(() => {
    const el = boxRef.current;
    const onWheel = (e: WheelEvent) => {
      e.preventDefault();
      setZoom((z) => Math.min(8, Math.max(1, z * (e.deltaY < 0 ? 1.12 : 0.89))));
    };
    el?.addEventListener("wheel", onWheel, { passive: false });
    const onMove = (e: MouseEvent) => {
      if (!dragRef.current || zoomRef.current <= 1) return;
      movedRef.current = true;
      setPan({ x: e.clientX - dragRef.current.x, y: e.clientY - dragRef.current.y });
    };
    const onUp = () => {
      dragRef.current = null;
      setDragging(false);
    };
    window.addEventListener("mousemove", onMove);
    window.addEventListener("mouseup", onUp);
    return () => {
      el?.removeEventListener("wheel", onWheel);
      window.removeEventListener("mousemove", onMove);
      window.removeEventListener("mouseup", onUp);
    };
  }, []);

  return (
    <div
      ref={boxRef}
      className="fixed inset-0 z-50 grid place-items-center p-4"
      style={{
        background: "rgba(0,0,0,.85)",
        overflow: "hidden",
        cursor: zoom > 1 ? (dragging ? "grabbing" : "grab") : "default",
      }}
      onMouseDown={(e) => {
        if (e.button !== 0 || zoom <= 1) return;
        dragRef.current = { x: e.clientX - pan.x, y: e.clientY - pan.y };
        movedRef.current = false;
        setDragging(true);
      }}
      onClick={() => {
        // Jangan tutup kalau baru saja menggeser (bukan klik sungguhan).
        if (movedRef.current) {
          movedRef.current = false;
          return;
        }
        onClose();
      }}
    >
      <button
        type="button"
        onClick={onClose}
        className="btn btn-sm"
        style={{ position: "absolute", right: 16, top: 16, background: "rgba(255,255,255,.12)", color: "#fff", zIndex: 2 }}
      >
        ✕ Tutup
      </button>

      <div className="flex items-center gap-1" style={{ position: "absolute", left: 16, top: 16, zIndex: 2 }}>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setZoom((z) => Math.max(1, z * 0.8)); }}
          className="btn btn-sm"
          style={{ background: "rgba(255,255,255,.12)", color: "#fff" }}
          title="Perkecil"
        >
          −
        </button>
        <span className="mono" style={{ color: "rgba(255,255,255,.8)", fontSize: 12, minWidth: 42, textAlign: "center" }}>
          {Math.round(zoom * 100)}%
        </span>
        <button
          type="button"
          onClick={(e) => { e.stopPropagation(); setZoom((z) => Math.min(8, z * 1.25)); }}
          className="btn btn-sm"
          style={{ background: "rgba(255,255,255,.12)", color: "#fff" }}
          title="Perbesar"
        >
          +
        </button>
      </div>

      {/* eslint-disable-next-line @next/next/no-img-element */}
      <img
        src={src}
        alt={alt}
        draggable={false}
        onClick={(e) => e.stopPropagation()}
        onDoubleClick={(e) => {
          e.stopPropagation();
          setZoom(1);
          setPan({ x: 0, y: 0 });
        }}
        style={{
          maxHeight: "90vh",
          maxWidth: "100%",
          borderRadius: 8,
          objectFit: "contain",
          background: "#fff",
          transform: `translate(${pan.x}px, ${pan.y}px) scale(${zoom})`,
          transition: dragging ? "none" : "transform .08s ease-out",
          userSelect: "none",
        }}
      />

      <div
        style={{
          position: "absolute", bottom: 14, left: 0, right: 0, textAlign: "center",
          fontSize: 12, color: "rgba(255,255,255,.6)", pointerEvents: "none", padding: "0 16px",
        }}
      >
        {caption ? <div style={{ marginBottom: 4, color: "rgba(255,255,255,.85)" }}>{caption}</div> : null}
        Scroll untuk zoom · seret untuk geser · klik dua kali untuk reset
      </div>
    </div>
  );
}
