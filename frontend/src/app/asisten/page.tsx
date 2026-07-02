"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import Markdown from "@/components/Markdown";
import {
  ApiError,
  aiChat,
  aiChatImage,
  downloadBlob,
  exportBandingRangka,
  exportRepairKit,
  getAiStatus,
  submitAiFeedback,
  type AIBandingExport,
  type AIChatTurn,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

type Msg = AIChatTurn & {
  tools?: string[];
  photo?: string;
  repairkitModels?: string[];
  bandingExports?: AIBandingExport[];
  at?: number; // epoch ms — jam pesan
  rating?: "up" | "down"; // umpan balik user atas jawaban ini
};

const SUGGESTIONS = [
  "Cek stok part WG9925520270",
  "Cari part rem depan",
  "Repair kit transmisi HW19709XST",
  "Gudang apa saja yang tersedia?",
];

// Nama tool internal → label sumber data yang ramah untuk user.
const TOOL_LABELS: Record<string, string> = {
  cari_part: "Katalog part",
  detail_part: "Detail part",
  info_aplikasi: "Status aplikasi",
  daftar_unit: "Daftar unit",
  cari_kode_kesalahan: "Kode kesalahan",
  cari_filter_shantui: "Filter Shantui",
  repair_kit_transmisi: "Repair kit transmisi",
  daftar_transmisi_assy: "Transmisi assy",
  banding_assy: "Banding isi assy",
  isi_assy: "Isi assy",
  banding_kategori: "Banding kategori",
  isi_kategori: "Isi kategori",
  part_termasuk_assy: "Pelacak assy",
  cek_kendaraan: "EPC · spesifikasi VIN",
  bom_dari_rangka: "EPC · BOM unit",
  banding_rangka: "EPC · banding unit",
  part_aus_dari_rangka: "EPC · part per-VIN",
  kategori_unit: "EPC · kategori unit",
  uraikan_assembly: "EPC · isi assembly",
  uraikan_mesin: "EPC Weichai · mesin",
  pengganti_part: "EPC Weichai · pengganti",
  repair_kit_mesin: "EPC Weichai · repair kit",
  unit_dari_part: "EPC · unit pemakai",
  cek_populasi: "Populasi unit",
  pesanan_saya: "Pesanan saya",
  detail_pesanan: "Detail pesanan",
  rekap_penjualan: "Rekap penjualan",
  daftar_pesanan: "Daftar pesanan",
  harga_sims: "Harga SIMS",
};

// Kunci penyimpanan chat agar tidak hilang saat pindah menu lalu kembali.
// sessionStorage = bertahan selama tab browser terbuka (termasuk navigasi
// antar-menu & refresh), otomatis bersih saat tab ditutup.
const CHAT_KEY = "maspart_asisten_chat";

/* ── Ikon garis (SVG inline, tanpa dependency) ── */
function Icon({ d, size = 16, sw = 1.8 }: { d: string; size?: number; sw?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth={sw}
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden
      style={{ flexShrink: 0 }}
    >
      <path d={d} />
    </svg>
  );
}
const IC = {
  send: "M22 2 11 13 M22 2 15 22 11 13 2 9 22 2",
  camera:
    "M23 19a2 2 0 0 1-2 2H3a2 2 0 0 1-2-2V8a2 2 0 0 1 2-2h4l2-3h6l2 3h4a2 2 0 0 1 2 2z M16 13a4 4 0 1 1-8 0 4 4 0 0 1 8 0z",
  trash:
    "M3 6h18 M8 6V4a2 2 0 0 1 2-2h4a2 2 0 0 1 2 2v2 M19 6v14a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V6",
  copy:
    "M20 9h-9a2 2 0 0 0-2 2v9a2 2 0 0 0 2 2h9a2 2 0 0 0 2-2v-9a2 2 0 0 0-2-2z M5 15H4a2 2 0 0 1-2-2V4a2 2 0 0 1 2-2h9a2 2 0 0 1 2 2v1",
  check: "M20 6 9 17l-5-5",
  bot: "M12 7V4 M9.5 4h5 M7 7h10a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H7a2 2 0 0 1-2-2V9a2 2 0 0 1 2-2z M9.5 13h.01 M14.5 13h.01 M5 12H3 M21 12h-2",
  download: "M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4 M7 10l5 5 5-5 M12 15V3",
  info: "M12 22a10 10 0 1 0 0-20 10 10 0 0 0 0 20z M12 16v-4 M12 8h.01",
  thumbUp: "M7 10v11 M2 13v6a2 2 0 0 0 2 2h13.5a2 2 0 0 0 2-1.7l1.2-8A1.5 1.5 0 0 0 18.2 9H14V5a2 2 0 0 0-2-2l-3 7H7",
  thumbDown: "M17 14V3 M22 11V5a2 2 0 0 0-2-2H6.5a2 2 0 0 0-2 1.7l-1.2 8A1.5 1.5 0 0 0 4.8 15H9v4a2 2 0 0 0 2 2l3-7h3",
  sheet: "M5 3h14a1 1 0 0 1 1 1v16a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1z M4 9h16 M4 15h16 M10 3v18",
};

function Avatar({ size = 30 }: { size?: number }) {
  return (
    <div
      style={{
        width: size,
        height: size,
        borderRadius: 99,
        flexShrink: 0,
        background: "linear-gradient(135deg, var(--brand-600), var(--brand-500))",
        color: "#fff",
        display: "grid",
        placeItems: "center",
        boxShadow: "var(--shadow-1)",
      }}
    >
      <Icon d={IC.bot} size={Math.round(size * 0.58)} sw={2} />
    </div>
  );
}

function fmtTime(at?: number): string {
  if (!at) return "";
  try {
    return new Intl.DateTimeFormat("id-ID", { hour: "2-digit", minute: "2-digit" }).format(at);
  } catch {
    return "";
  }
}

export default function AsistenPage() {
  const router = useRouter();
  const [msgs, setMsgs] = useState<Msg[]>([]);
  const [input, setInput] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [available, setAvailable] = useState<boolean | null>(null);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
  const firstSave = useRef(true);

  useEffect(() => {
    const token = getToken();
    if (!token) return router.replace("/login");
    getAiStatus(token)
      .then((s) => setAvailable(s.available))
      .catch((err) => {
        if (err instanceof ApiError && err.status === 401) {
          clearSession();
          router.replace("/login");
          return;
        }
        setAvailable(false);
      });
  }, [router]);

  useEffect(() => {
    scrollRef.current?.scrollTo({ top: scrollRef.current.scrollHeight, behavior: "smooth" });
  }, [msgs, busy]);

  // Muat chat tersimpan saat halaman dibuka kembali (mis. balik dari menu lain).
  useEffect(() => {
    try {
      const raw = sessionStorage.getItem(CHAT_KEY);
      if (raw) {
        const saved = JSON.parse(raw);
        if (Array.isArray(saved) && saved.length) setMsgs(saved);
      }
    } catch {
      /* abaikan storage rusak */
    }
  }, []);

  // Simpan chat tiap berubah. Lewati simpan pertama (saat mount) agar tidak
  // menimpa data sebelum sempat dimuat. Batasi 60 pesan terakhir biar ringan.
  useEffect(() => {
    if (firstSave.current) {
      firstSave.current = false;
      return;
    }
    try {
      if (msgs.length) sessionStorage.setItem(CHAT_KEY, JSON.stringify(msgs.slice(-60)));
      else sessionStorage.removeItem(CHAT_KEY);
    } catch {
      /* storage penuh/diblokir — abaikan */
    }
  }, [msgs]);

  function resetTextarea() {
    const ta = taRef.current;
    if (ta) ta.style.height = "auto";
  }

  // Textarea membesar mengikuti isi (maks ~5 baris) — nyaman untuk pesan panjang.
  function autoGrow() {
    const ta = taRef.current;
    if (!ta) return;
    ta.style.height = "auto";
    ta.style.height = Math.min(ta.scrollHeight, 140) + "px";
  }

  async function send(text: string) {
    const body = text.trim();
    if (!body || busy) return;
    const token = getToken();
    if (!token) return router.replace("/login");

    setError(null);
    const next: Msg[] = [...msgs, { role: "user", content: body, at: Date.now() }];
    setMsgs(next);
    setInput("");
    resetTextarea();
    setBusy(true);
    try {
      const payload: AIChatTurn[] = next.map((m) => ({ role: m.role, content: m.content }));
      const res = await aiChat(token, payload);
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          content: res.reply || "(tidak ada jawaban)",
          tools: res.tools_used,
          repairkitModels: res.repairkit_models,
          bandingExports: res.banding_exports,
          at: Date.now(),
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      const msg = err instanceof Error ? err.message : "Gagal menghubungi asisten.";
      setError(msg);
      // Kembalikan input agar bisa coba lagi.
      setInput(body);
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  }

  async function sendWithPhoto(file: File) {
    if (busy) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!file.type.startsWith("image/")) {
      setError("File harus berupa gambar.");
      return;
    }
    setError(null);
    const caption = input.trim();
    const userText =
      caption || "Tolong kenali part di foto ini (stok, harga, dipakai di unit apa).";
    const preview = URL.createObjectURL(file);
    const next: Msg[] = [...msgs, { role: "user", content: userText, photo: preview, at: Date.now() }];
    setMsgs(next);
    setInput("");
    resetTextarea();
    setBusy(true);
    try {
      const payload: AIChatTurn[] = next.map((m) => ({ role: m.role, content: m.content }));
      const res = await aiChatImage(token, payload, file);
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          content: res.reply || "(tidak ada jawaban)",
          tools: res.tools_used,
          repairkitModels: res.repairkit_models,
          bandingExports: res.banding_exports,
          at: Date.now(),
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal mengirim foto.");
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
      if (fileRef.current) fileRef.current.value = "";
      taRef.current?.focus();
    }
  }

  function clearChat() {
    setMsgs([]);
    setError(null);
    try {
      sessionStorage.removeItem(CHAT_KEY);
    } catch {
      /* abaikan */
    }
  }

  // Kirim 👍/👎 atas jawaban asisten pada indeks `idx`. Menyertakan pertanyaan
  // user sebelumnya + beberapa giliran terakhir sebagai konteks untuk review.
  async function sendFeedback(idx: number, rating: "up" | "down", note?: string) {
    const token = getToken();
    if (!token) return;
    const target = msgs[idx];
    if (!target || target.role !== "assistant") return;
    // Pertanyaan = pesan user tepat sebelum jawaban ini.
    let question = "";
    for (let j = idx - 1; j >= 0; j--) {
      if (msgs[j].role === "user") {
        question = msgs[j].content;
        break;
      }
    }
    const context: AIChatTurn[] = msgs
      .slice(Math.max(0, idx - 5), idx + 1)
      .map((m) => ({ role: m.role, content: m.content }));
    // Optimistis: tandai langsung supaya UI responsif; kembalikan bila gagal.
    setMsgs((m) => m.map((x, i) => (i === idx ? { ...x, rating } : x)));
    try {
      await submitAiFeedback(token, {
        rating,
        question,
        answer: target.content,
        tools: target.tools,
        note,
        context,
      });
    } catch {
      setMsgs((m) => m.map((x, i) => (i === idx ? { ...x, rating: undefined } : x)));
      setError("Gagal menyimpan umpan balik. Coba lagi.");
    }
  }

  function onKeyDown(e: React.KeyboardEvent<HTMLTextAreaElement>) {
    if (e.key === "Enter" && !e.shiftKey) {
      e.preventDefault();
      send(input);
    }
  }

  return (
    <AppShell active="/asisten" title="Asisten AI" sub="Tanya apa saja tentang part, stok, harga, dan pesanan">
      <div className="mx-auto flex h-full w-full max-w-3xl flex-col px-4 py-4 sm:px-6">
        {available === false && (
          <div className="alert alert-error" style={{ marginBottom: 12 }}>
            Asisten AI belum aktif. Set <code>DEEPSEEK_API_KEY</code> di <code>backend/.env</code> lalu restart backend.
          </div>
        )}

        {/* Kartu chat: header identitas · area pesan · input — satu kesatuan */}
        <div
          className="surface flex flex-1 flex-col"
          style={{ minHeight: 380, overflow: "hidden" }}
        >
          {/* Header */}
          <div
            style={{
              display: "flex",
              alignItems: "center",
              gap: 10,
              padding: "10px 14px",
              borderBottom: "1px solid var(--ink-150)",
              background: "var(--paper)",
            }}
          >
            <Avatar size={32} />
            <div style={{ flex: 1, minWidth: 0 }}>
              <div style={{ fontWeight: 650, fontSize: 13.5, color: "var(--ink-900)" }}>
                Asisten MASPART
              </div>
              <div
                style={{
                  fontSize: 11,
                  display: "flex",
                  alignItems: "center",
                  gap: 5,
                  color: available === false ? "var(--danger-600)" : "var(--brand-700)",
                }}
              >
                <span className="status-dot" />
                {available === false ? "Nonaktif" : "Online · terhubung ke data live"}
              </div>
            </div>
            {msgs.length > 0 && (
              <button
                className="btn btn-ghost btn-sm"
                onClick={clearChat}
                disabled={busy}
                title="Mulai percakapan baru"
                style={{ gap: 5 }}
              >
                <Icon d={IC.trash} size={13} /> Chat baru
              </button>
            )}
          </div>

          {/* Area pesan */}
          <div
            ref={scrollRef}
            style={{
              flex: 1,
              overflow: "auto",
              padding: "16px 16px 12px",
              display: "flex",
              flexDirection: "column",
              gap: 14,
              background: "var(--canvas)",
            }}
          >
            {msgs.length === 0 ? (
              <div className="grid flex-1 place-items-center" style={{ textAlign: "center" }}>
                <div style={{ display: "grid", gap: 14, maxWidth: 480, justifyItems: "center" }}>
                  <Avatar size={52} />
                  <div>
                    <div style={{ fontWeight: 700, fontSize: 16.5, color: "var(--ink-900)" }}>
                      Selamat datang di Asisten MASPART
                    </div>
                    <div
                      style={{
                        fontSize: 13,
                        color: "var(--ink-500)",
                        marginTop: 6,
                        lineHeight: 1.6,
                      }}
                    >
                      Cek stok per gudang, harga, part per unit (EPC per-VIN), kode kesalahan,
                      repair kit, hingga pengenalan part dari <b>foto</b> — semua dari data live.
                    </div>
                  </div>
                  <div style={{ display: "flex", flexWrap: "wrap", gap: 8, justifyContent: "center" }}>
                    {SUGGESTIONS.map((s) => (
                      <button
                        key={s}
                        className="btn btn-secondary btn-sm"
                        onClick={() => send(s)}
                        disabled={busy || available === false}
                        style={{ fontSize: 12, borderRadius: 99 }}
                      >
                        {s}
                      </button>
                    ))}
                  </div>
                  <div
                    style={{
                      display: "inline-flex",
                      alignItems: "center",
                      gap: 6,
                      fontSize: 11.5,
                      color: "var(--ink-500)",
                      background: "var(--brand-50)",
                      border: "1px solid var(--brand-100)",
                      borderRadius: 99,
                      padding: "5px 12px",
                    }}
                  >
                    <span style={{ color: "var(--brand-700)", display: "inline-flex" }}>
                      <Icon d={IC.info} size={13} />
                    </span>
                    Sebutkan <b>nomor rangka (VIN)</b> agar jawaban part persis untuk unit Anda.
                  </div>
                </div>
              </div>
            ) : (
              msgs.map((m, i) => (
                <Bubble key={i} m={m} onFeedback={(r, note) => sendFeedback(i, r, note)} />
              ))
            )}

            {busy && (
              <div className="chat-bubble-in" style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
                <Avatar />
                <div
                  style={{
                    background: "var(--paper)",
                    border: "1px solid var(--ink-150)",
                    borderRadius: 14,
                    borderTopLeftRadius: 4,
                    padding: "12px 16px",
                    boxShadow: "var(--shadow-1)",
                  }}
                >
                  <span className="typing-dots">
                    <span />
                    <span />
                    <span />
                  </span>
                </div>
              </div>
            )}
          </div>

          {error && (
            <div className="alert alert-error" style={{ margin: "8px 12px 0" }}>
              {error}
            </div>
          )}

          {/* Input */}
          <div style={{ borderTop: "1px solid var(--ink-150)", background: "var(--paper)", padding: "10px 12px 8px" }}>
            <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
              <input
                ref={fileRef}
                type="file"
                accept="image/*"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (f) sendWithPhoto(f);
                }}
              />
              <button
                className="btn btn-ghost"
                title="Cari part dari foto"
                onClick={() => fileRef.current?.click()}
                disabled={busy || available === false}
                style={{ padding: "0 10px", color: "var(--ink-600)" }}
              >
                <Icon d={IC.camera} size={19} />
              </button>
              <textarea
                ref={taRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  autoGrow();
                }}
                onKeyDown={onKeyDown}
                placeholder={available === false ? "Asisten belum aktif…" : "Tulis pertanyaan…"}
                disabled={busy || available === false}
                rows={1}
                style={{
                  flex: 1,
                  resize: "none",
                  border: "none",
                  outline: "none",
                  background: "transparent",
                  fontSize: 14,
                  padding: "8px 6px",
                  maxHeight: 140,
                  lineHeight: 1.4,
                  color: "var(--ink-800)",
                }}
              />
              <button
                className="btn btn-primary"
                onClick={() => send(input)}
                disabled={busy || !input.trim() || available === false}
                style={{ gap: 6 }}
              >
                <Icon d={IC.send} size={14} /> Kirim
              </button>
            </div>
            <div
              style={{
                fontSize: 10.5,
                color: "var(--ink-400)",
                padding: "6px 4px 0",
                display: "flex",
                gap: 12,
                flexWrap: "wrap",
              }}
            >
              <span>
                <span className="kbd">Enter</span> kirim · <span className="kbd">Shift+Enter</span> baris baru
              </span>
              <span>Jawaban part paling akurat bila menyertakan nomor rangka (VIN).</span>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// Kartu file gaya Claude: ikon spreadsheet + nama file + tombol Unduh.
function ExcelCard({ exp }: { exp: AIBandingExport }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const kat = exp.kategori_nama && exp.kategori_nama !== "semua part" ? ` · ${exp.kategori_nama}` : "";
  const fname = `Perbandingan ${exp.rangka_1} vs ${exp.rangka_2}`;

  async function download() {
    const token = getToken();
    if (!token) return;
    setBusy(true);
    setErr(null);
    try {
      const blob = await exportBandingRangka(token, {
        rangka_1: exp.rangka_1,
        rangka_2: exp.rangka_2,
        kategori: exp.kategori,
      });
      downloadBlob(blob, `${fname.replace(/\s+/g, "_")}.xlsx`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal mengunduh Excel");
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ marginTop: 8 }}>
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: 12,
          maxWidth: 420,
          padding: "10px 12px",
          border: "1px solid var(--ink-200)",
          borderRadius: 12,
          background: "var(--paper)",
          boxShadow: "var(--shadow-1)",
        }}
      >
        <div
          style={{
            width: 38,
            height: 38,
            borderRadius: 8,
            flexShrink: 0,
            background: "var(--brand-50)",
            color: "var(--brand-700)",
            display: "grid",
            placeItems: "center",
            border: "1px solid var(--brand-100)",
          }}
        >
          <Icon d={IC.sheet} size={20} />
        </div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div
            style={{
              fontSize: 13,
              fontWeight: 600,
              color: "var(--ink-900)",
              whiteSpace: "nowrap",
              overflow: "hidden",
              textOverflow: "ellipsis",
            }}
          >
            {fname}
          </div>
          <div style={{ fontSize: 11, color: "var(--ink-500)" }}>Spreadsheet · XLSX{kat}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={download} disabled={busy} style={{ gap: 5 }}>
          <Icon d={IC.download} size={13} />
          {busy ? "Menyiapkan…" : "Unduh"}
        </button>
      </div>
      {err && <div style={{ fontSize: 11.5, color: "var(--danger-600)", marginTop: 4 }}>{err}</div>}
    </div>
  );
}

function RepairKitDownloads({ models }: { models: string[] }) {
  const [dl, setDl] = useState<string | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const uniq = Array.from(new Set(models)).filter(Boolean);
  if (uniq.length === 0) return null;

  async function download(model: string) {
    const token = getToken();
    if (!token) return;
    setDl(model);
    setErr(null);
    try {
      const blob = await exportRepairKit(token, model);
      downloadBlob(blob, `repairkit_transmisi_${model}.xlsx`);
    } catch (e) {
      setErr(e instanceof Error ? e.message : "Gagal mengunduh Excel");
    } finally {
      setDl(null);
    }
  }

  return (
    <div style={{ marginTop: 8, display: "flex", flexWrap: "wrap", gap: 6, alignItems: "center" }}>
      {uniq.map((model) => (
        <button
          key={model}
          className="btn btn-secondary btn-sm"
          onClick={() => download(model)}
          disabled={dl !== null}
          style={{ fontSize: 12, gap: 5 }}
          title={`Unduh Excel repair kit ${model}`}
        >
          <Icon d={IC.download} size={13} />
          {dl === model ? "Menyiapkan…" : `Excel ${model}`}
        </button>
      ))}
      {err && <span style={{ fontSize: 11.5, color: "var(--danger-600)" }}>{err}</span>}
    </div>
  );
}

function CopyBtn({ text }: { text: string }) {
  const [ok, setOk] = useState(false);
  return (
    <button
      onClick={async () => {
        try {
          await navigator.clipboard.writeText(text);
          setOk(true);
          setTimeout(() => setOk(false), 1500);
        } catch {
          /* clipboard diblokir — abaikan */
        }
      }}
      title="Salin jawaban"
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: 4,
        border: "none",
        background: "transparent",
        cursor: "pointer",
        color: ok ? "var(--brand-700)" : "var(--ink-400)",
        fontSize: 10.5,
        padding: "2px 4px",
        borderRadius: 4,
      }}
    >
      <Icon d={ok ? IC.check : IC.copy} size={12} /> {ok ? "Tersalin" : "Salin"}
    </button>
  );
}

function FeedbackButtons({
  rating,
  onFeedback,
}: {
  rating?: "up" | "down";
  onFeedback: (r: "up" | "down", note?: string) => void;
}) {
  const [noteOpen, setNoteOpen] = useState(false);
  const [note, setNote] = useState("");

  // Sudah dinilai → tampilkan konfirmasi ringkas (tak bisa diubah, cegah spam).
  if (rating) {
    return (
      <span
        style={{
          display: "inline-flex",
          alignItems: "center",
          gap: 5,
          fontSize: 10.5,
          color: rating === "up" ? "var(--brand-700)" : "var(--warn-600)",
        }}
      >
        <Icon d={rating === "up" ? IC.thumbUp : IC.thumbDown} size={12} />
        {rating === "up" ? "Terima kasih!" : "Masukan terkirim — kami perbaiki."}
      </span>
    );
  }

  return (
    <>
      <span style={{ display: "inline-flex", alignItems: "center", gap: 2 }}>
        <button
          onClick={() => onFeedback("up")}
          title="Jawaban ini membantu"
          style={fbBtnStyle}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--brand-700)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--ink-400)")}
        >
          <Icon d={IC.thumbUp} size={13} />
        </button>
        <button
          onClick={() => setNoteOpen((v) => !v)}
          title="Jawaban ini kurang tepat"
          style={fbBtnStyle}
          onMouseEnter={(e) => (e.currentTarget.style.color = "var(--warn-600)")}
          onMouseLeave={(e) => (e.currentTarget.style.color = "var(--ink-400)")}
        >
          <Icon d={IC.thumbDown} size={13} />
        </button>
      </span>
      {noteOpen && (
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: 4 }}>
          <input
            autoFocus
            value={note}
            onChange={(e) => setNote(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === "Enter") onFeedback("down", note.trim() || undefined);
              if (e.key === "Escape") setNoteOpen(false);
            }}
            placeholder="Apa yang salah? (opsional)"
            style={{
              fontSize: 11.5,
              padding: "3px 8px",
              border: "1px solid var(--ink-200)",
              borderRadius: 99,
              outline: "none",
              width: 190,
              color: "var(--ink-800)",
            }}
          />
          <button
            className="btn btn-secondary btn-sm"
            style={{ fontSize: 11, height: 24, borderRadius: 99 }}
            onClick={() => onFeedback("down", note.trim() || undefined)}
          >
            Kirim
          </button>
        </span>
      )}
    </>
  );
}

const fbBtnStyle: React.CSSProperties = {
  border: "none",
  background: "transparent",
  cursor: "pointer",
  color: "var(--ink-400)",
  padding: "2px 4px",
  borderRadius: 4,
  display: "inline-flex",
  transition: "color 0.12s",
};

function Bubble({
  m,
  onFeedback,
}: {
  m: Msg;
  onFeedback?: (r: "up" | "down", note?: string) => void;
}) {
  const isUser = m.role === "user";
  const time = fmtTime(m.at);

  if (isUser) {
    return (
      <div className="chat-bubble-in" style={{ alignSelf: "flex-end", maxWidth: "85%" }}>
        <div
          style={{
            padding: "9px 13px",
            borderRadius: 14,
            borderBottomRightRadius: 4,
            background: "var(--brand-600)",
            color: "#fff",
            fontSize: 14,
            lineHeight: 1.5,
            whiteSpace: "pre-wrap",
            wordBreak: "break-word",
            boxShadow: "var(--shadow-1)",
          }}
        >
          {m.photo && (
            // eslint-disable-next-line @next/next/no-img-element
            <img
              src={m.photo}
              alt="foto part"
              onError={(e) => {
                // URL blob foto bisa mati setelah refresh penuh — sembunyikan
                // agar tidak tampil ikon gambar rusak.
                (e.currentTarget as HTMLImageElement).style.display = "none";
              }}
              style={{ maxWidth: 200, borderRadius: 10, marginBottom: m.content ? 8 : 0, display: "block" }}
            />
          )}
          {m.content}
        </div>
        {time && (
          <div style={{ fontSize: 10.5, color: "var(--ink-400)", marginTop: 3, textAlign: "right", paddingRight: 4 }}>
            {time}
          </div>
        )}
      </div>
    );
  }

  const tools = Array.from(new Set(m.tools || []));
  return (
    <div className="chat-bubble-in" style={{ display: "flex", gap: 10, alignItems: "flex-start", maxWidth: "96%" }}>
      <Avatar />
      <div style={{ minWidth: 0, flex: 1 }}>
        <div
          style={{
            padding: "10px 14px",
            borderRadius: 14,
            borderTopLeftRadius: 4,
            background: "var(--paper)",
            color: "var(--ink-800)",
            fontSize: 14,
            lineHeight: 1.5,
            wordBreak: "break-word",
            border: "1px solid var(--ink-150)",
            boxShadow: "var(--shadow-1)",
          }}
        >
          <Markdown content={m.content} />
        </div>
        {m.repairkitModels && m.repairkitModels.length > 0 && (
          <RepairKitDownloads models={m.repairkitModels} />
        )}
        {m.bandingExports?.map((exp, i) => (
          <ExcelCard key={i} exp={exp} />
        ))}
        {(tools.length > 0 || time || onFeedback) && (
          <div
            style={{
              display: "flex",
              flexWrap: "wrap",
              alignItems: "center",
              gap: 6,
              marginTop: 5,
              paddingLeft: 2,
            }}
          >
            {tools.length > 0 && (
              <>
                <span style={{ fontSize: 10.5, color: "var(--ink-400)" }}>Sumber:</span>
                {tools.map((t) => (
                  <span key={t} className="pill" style={{ height: 20, fontSize: 10.5, padding: "0 7px" }}>
                    {TOOL_LABELS[t] || t}
                  </span>
                ))}
              </>
            )}
            {time && <span style={{ fontSize: 10.5, color: "var(--ink-400)" }}>{time}</span>}
            <CopyBtn text={m.content} />
            {onFeedback && (
              <>
                <span style={{ color: "var(--ink-200)" }}>·</span>
                <FeedbackButtons rating={m.rating} onFeedback={onFeedback} />
              </>
            )}
          </div>
        )}
      </div>
    </div>
  );
}
