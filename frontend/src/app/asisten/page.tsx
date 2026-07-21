"use client";

import { useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import ImageLightbox from "@/components/ImageLightbox";
import Markdown from "@/components/Markdown";
import {
  ApiError,
  aiChat,
  aiChatSheet,
  aiChatStream,
  downloadBlob,
  exportAiExcel,
  exportBandingRangka,
  exportRepairKit,
  getAiStatus,
  submitAiFeedback,
  type AIBandingExport,
  type AIChatResult,
  type AIChatTurn,
  type AIExcelExport,
  type AIExplodedImage,
  type AISheetSummary,
} from "@/lib/api";
import { clearSession, getToken } from "@/lib/auth";

type Msg = AIChatTurn & {
  tools?: string[];
  sheetName?: string;  // nama file Excel yang dilampirkan user di pesan ini
  sheet?: AISheetSummary; // ringkasan kolom hasil deteksi server
  repairkitModels?: string[];
  bandingExports?: AIBandingExport[];
  excelExports?: AIExcelExport[];
  explodedImages?: AIExplodedImage[]; // gambar exploded view inline (besar)
  streamStatus?: string[]; // status langkah live saat streaming (sebelum jawaban)
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
  diagram_wiring: "Diagram wiring",
  cari_filter_shantui: "Filter Shantui",
  jadwal_perawatan: "Jadwal perawatan",
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
  banding_part_armada: "Banding part armada (populasi + EPC)",
  pesanan_saya: "Pesanan saya",
  detail_pesanan: "Detail pesanan",
  rekap_penjualan: "Rekap penjualan",
  daftar_pesanan: "Daftar pesanan",
  // Harga SIMS = harga MODAL ber-CNY; asisten menyajikan apa adanya (tanpa
  // konversi) kecuali user minta rupiah — satuannya disebut agar tak tertukar
  // dengan harga jual Accurate yang ber-Rupiah.
  harga_sims: "Harga SIMS (CNY)",
  buat_excel: "Export Excel",
  katalog_kategori: "EPC · katalog bergambar",
  sheet_ringkasan: "Excel lampiran",
  sheet_isi_kolom: "Excel lampiran · isi kolom",
  buat_penawaran: "Accurate · Penawaran",
};

// Peran kolom hasil deteksi server → label ramah.
const PERAN_LABEL: Record<string, string> = {
  part_number: "Part Number",
  part_name: "Nama part",
  stok: "Stok",
  qty: "Qty",
  harga: "Harga",
  lain: "—",
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
  paperclip:
    "M21.4 11.05 12.25 20.2a6 6 0 0 1-8.49-8.49l9.2-9.19a4 4 0 0 1 5.66 5.66l-9.2 9.19a2 2 0 0 1-2.83-2.83l8.49-8.48",
  x: "M18 6 6 18 M6 6l12 12",
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
  const [allowed, setAllowed] = useState(true);   // menu 'ai' dimatikan admin? (Menu Control)
  // File yang sudah DIPILIH tapi BELUM dikirim — user mengetik dulu maunya apa
  // ("isikan stok"), baru tekan Kirim. Gaya Claude.
  const [pendingSheet, setPendingSheet] = useState<File | null>(null);
  // Lampiran Excel aktif: dipegang server (TTL 2 jam). Dikirim ulang tiap giliran
  // agar follow-up "isikan stoknya" tetap mengacu ke file yang sama.
  const [sheetId, setSheetId] = useState("");
  const [sheetName, setSheetName] = useState("");
  // Seret-lepas file ke area chat. dragDepth: dragenter/dragleave ikut terpicu
  // di tiap anak elemen — hitung kedalaman agar overlay tidak kedip.
  const [dragOver, setDragOver] = useState(false);
  const dragDepth = useRef(0);
  const scrollRef = useRef<HTMLDivElement>(null);
  const taRef = useRef<HTMLTextAreaElement>(null);
  const sheetRef = useRef<HTMLInputElement>(null);
  const firstSave = useRef(true);

  useEffect(() => {
    const token = getToken();
    if (!token) return router.replace("/login");
    getAiStatus(token)
      .then((s) => {
        setAvailable(s.available);
        setAllowed(s.allowed !== false);
      })
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
    if (busy) return;
    // File yang baru dilampirkan dikirim bersama pesan ini (bukan saat dipilih).
    if (pendingSheet) return sendWithSheet(pendingSheet, body);
    if (!body) return;
    const token = getToken();
    if (!token) return router.replace("/login");

    setError(null);
    const next: Msg[] = [...msgs, { role: "user", content: body, at: Date.now() }];
    setMsgs(next);
    setInput("");
    resetTextarea();
    setBusy(true);
    // Placeholder jawaban yang menampilkan STATUS langkah live selama streaming.
    setMsgs((m) => [...m, { role: "assistant", content: "", streamStatus: [], at: Date.now() }]);

    const applyResult = (res: AIChatResult) =>
      setMsgs((m) => {
        const copy = [...m];
        copy[copy.length - 1] = {
          role: "assistant",
          content: res.reply || "(tidak ada jawaban)",
          tools: res.tools_used,
          repairkitModels: res.repairkit_models,
          bandingExports: res.banding_exports,
          excelExports: res.excel_exports,
          explodedImages: res.exploded_images,
          at: Date.now(),
        };
        return copy;
      });

    try {
      const payload: AIChatTurn[] = next.map((m) => ({ role: m.role, content: m.content }));
      let res: AIChatResult;
      try {
        res = await aiChatStream(token, payload, sheetId, (label) =>
          setMsgs((m) => {
            const copy = [...m];
            const last = copy[copy.length - 1];
            if (last?.role === "assistant" && last.streamStatus) {
              const prev = last.streamStatus[last.streamStatus.length - 1];
              if (prev !== label) {
                copy[copy.length - 1] = { ...last, streamStatus: [...last.streamStatus, label] };
              }
            }
            return copy;
          }),
        );
      } catch (streamErr) {
        // Streaming gagal (mis. proxy buffering / SSE tak didukung) → fallback ke /chat.
        if (streamErr instanceof ApiError && streamErr.status === 401) throw streamErr;
        res = await aiChat(token, payload, sheetId);
      }
      applyResult(res);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      const msg = err instanceof Error ? err.message : "Gagal menghubungi asisten.";
      setError(msg);
      // Kembalikan input agar bisa coba lagi; buang placeholder + pesan user.
      setInput(body);
      setMsgs((m) => m.slice(0, -2));
    } finally {
      setBusy(false);
      taRef.current?.focus();
    }
  }

  // Unggah Excel: server membaca kolomnya & menyimpan sheet (TTL 2 jam) → sheet_id
  // dipakai giliran berikutnya ("isikan stoknya di kolom D"). Dipanggil dari send()
  // saat user menekan Kirim — memilih file hanya MELAMPIRKAN, tidak mengirim.
  async function sendWithSheet(file: File, caption: string) {
    if (busy) return;
    const token = getToken();
    if (!token) return router.replace("/login");
    setError(null);
    const userText = caption || `Saya lampirkan ${file.name}. Isinya apa saja?`;
    const next: Msg[] = [
      ...msgs,
      { role: "user", content: userText, sheetName: file.name, at: Date.now() },
    ];
    setMsgs(next);
    setInput("");
    setPendingSheet(null);
    resetTextarea();
    setBusy(true);
    try {
      const payload: AIChatTurn[] = next.map((m) => ({ role: m.role, content: m.content }));
      const res = await aiChatSheet(token, payload, file);
      if (res.sheet_id) {
        setSheetId(res.sheet_id);
        setSheetName(file.name);
      }
      setMsgs((m) => [
        ...m,
        {
          role: "assistant",
          content: res.reply || "(tidak ada jawaban)",
          tools: res.tools_used,
          repairkitModels: res.repairkit_models,
          bandingExports: res.banding_exports,
          excelExports: res.excel_exports,
          explodedImages: res.exploded_images,
          sheet: res.sheet,
          at: Date.now(),
        },
      ]);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal mengunggah Excel.");
      // Kembalikan lampiran & teks agar user bisa langsung coba kirim lagi.
      setPendingSheet(file);
      setInput(caption);
      setMsgs((m) => m.slice(0, -1));
    } finally {
      setBusy(false);
      if (sheetRef.current) sheetRef.current.value = "";
      taRef.current?.focus();
    }
  }

  function clearChat() {
    setMsgs([]);
    setError(null);
    setSheetId("");
    setSheetName("");
    setPendingSheet(null);
    if (sheetRef.current) sheetRef.current.value = "";
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

  // ── Seret-lepas file ke area chat ──────────────────────────────────────────
  // Excel .xlsx/.xlsm → hanya DILAMPIRKAN (perilaku tombol klip) — user mengetik
  // maunya dulu, baru tekan Kirim.
  function dragHasFiles(e: React.DragEvent) {
    return Array.from(e.dataTransfer.types).includes("Files");
  }

  function onDragEnter(e: React.DragEvent) {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    dragDepth.current += 1;
    setDragOver(true);
  }

  function onDragOver(e: React.DragEvent) {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
  }

  function onDragLeave(e: React.DragEvent) {
    if (!dragHasFiles(e)) return;
    dragDepth.current -= 1;
    if (dragDepth.current <= 0) {
      dragDepth.current = 0;
      setDragOver(false);
    }
  }

  function onDrop(e: React.DragEvent) {
    if (!dragHasFiles(e)) return;
    e.preventDefault();
    dragDepth.current = 0;
    setDragOver(false);
    if (busy || available === false) return;
    const f = e.dataTransfer.files?.[0];
    if (!f) return;
    if (/\.(xlsx|xlsm)$/i.test(f.name)) {
      setError(null);
      setPendingSheet(f);
      taRef.current?.focus();
      return;
    }
    setError("File tidak didukung — seret file Excel .xlsx atau .xlsm.");
  }

  return (
    <AppShell active="/asisten" title="Asisten AI" sub="Tanya apa saja tentang part, stok, harga, dan pesanan">
      <div className="flex h-full w-full flex-col">
        {available === false && (
          <div className="alert alert-error" style={{ margin: 12 }}>
            {allowed ? (
              <>Asisten AI belum aktif. Set <code>DEEPSEEK_API_KEY</code> di <code>backend/.env</code> lalu restart backend.</>
            ) : (
              <>Asisten AI dimatikan untuk akun ini oleh admin (Menu Control).</>
            )}
          </div>
        )}

        {/* Chat memenuhi seluruh area kerja: header identitas · area pesan · input */}
        <div
          className="flex flex-1 flex-col"
          style={{ minHeight: 0, overflow: "hidden", position: "relative" }}
          onDragEnter={onDragEnter}
          onDragOver={onDragOver}
          onDragLeave={onDragLeave}
          onDrop={onDrop}
        >
          {/* Overlay saat file diseret di atas area chat. pointerEvents none agar
              tidak mengganggu hitungan dragenter/dragleave kontainer. */}
          {dragOver && (
            <div
              style={{
                position: "absolute",
                inset: 0,
                zIndex: 30,
                pointerEvents: "none",
                display: "grid",
                placeItems: "center",
                background: "color-mix(in srgb, var(--paper) 82%, transparent)",
                backdropFilter: "blur(2px)",
              }}
            >
              <div
                style={{
                  display: "grid",
                  gap: 10,
                  justifyItems: "center",
                  border: "2px dashed var(--brand-400, var(--brand-700))",
                  borderRadius: 16,
                  background: "var(--brand-50)",
                  padding: "28px 36px",
                  margin: 16,
                  textAlign: "center",
                }}
              >
                <span style={{ color: "var(--brand-700)", display: "inline-flex" }}>
                  <Icon d={IC.paperclip} size={28} />
                </span>
                <div style={{ fontWeight: 700, fontSize: 14.5, color: "var(--ink-900)" }}>
                  Lepaskan file di sini
                </div>
                <div style={{ fontSize: 12, color: "var(--ink-500)", lineHeight: 1.5 }}>
                  Excel .xlsx/.xlsm terlampir dulu, kirim setelah mengetik perintah
                </div>
              </div>
            </div>
          )}

          {/* Header */}
          <div className="chat-head">
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
            className="chat-scroll"
            style={{
              flex: 1,
              overflow: "auto",
              display: "flex",
              flexDirection: "column",
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
                      hingga repair kit — semua dari data live.
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
              <div className="chat-bubble-in chat-row-ai">
                <div className="chat-bubble-ai">
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
            {/* File dipilih tapi BELUM dikirim — kartu pratinjau gaya Claude.
                User mengetik maunya ("isikan stok") lalu tekan Kirim. */}
            {pendingSheet && (
              <FilePreviewCard
                name={pendingSheet.name}
                onRemove={() => {
                  setPendingSheet(null);
                  if (sheetRef.current) sheetRef.current.value = "";
                }}
              />
            )}
            {/* Lampiran yang SUDAH terkirim & masih aktif di server (follow-up). */}
            {!pendingSheet && sheetName && (
              <div
                style={{
                  display: "inline-flex",
                  alignItems: "center",
                  gap: 7,
                  fontSize: 11.5,
                  color: "var(--brand-700)",
                  background: "var(--brand-50)",
                  border: "1px solid var(--brand-100)",
                  borderRadius: 99,
                  padding: "4px 6px 4px 10px",
                  marginBottom: 8,
                  maxWidth: "100%",
                }}
                title="File ini masih terlampir — asisten bisa mengisi kolomnya"
              >
                <Icon d={IC.sheet} size={13} />
                <span className="truncate" style={{ maxWidth: 200 }}>{sheetName}</span>
                <button
                  onClick={() => {
                    setSheetId("");
                    setSheetName("");
                  }}
                  title="Lepas lampiran"
                  aria-label="Lepas lampiran"
                  style={{
                    border: "none",
                    background: "transparent",
                    cursor: "pointer",
                    color: "var(--brand-700)",
                    display: "inline-flex",
                    padding: 2,
                  }}
                >
                  <Icon d={IC.x} size={12} />
                </button>
              </div>
            )}
            <div style={{ display: "flex", gap: 8, alignItems: "flex-end" }}>
              <input
                ref={sheetRef}
                type="file"
                accept=".xlsx,.xlsm"
                style={{ display: "none" }}
                onChange={(e) => {
                  const f = e.target.files?.[0];
                  if (!f) return;
                  if (!/\.(xlsx|xlsm)$/i.test(f.name)) {
                    setError("File harus Excel .xlsx atau .xlsm (bukan .xls / .csv).");
                    if (sheetRef.current) sheetRef.current.value = "";
                    return;
                  }
                  // Hanya DILAMPIRKAN. Terkirim saat user menekan Kirim.
                  setError(null);
                  setPendingSheet(f);
                  taRef.current?.focus();
                }}
              />
              <button
                className="btn btn-ghost"
                title="Lampirkan Excel (.xlsx) — asisten bisa isi stok/nama part/harga"
                onClick={() => sheetRef.current?.click()}
                disabled={busy || available === false}
                style={{ padding: "0 10px", color: "var(--ink-600)" }}
              >
                <Icon d={IC.paperclip} size={18} />
              </button>
              <textarea
                ref={taRef}
                value={input}
                onChange={(e) => {
                  setInput(e.target.value);
                  autoGrow();
                }}
                onKeyDown={onKeyDown}
                placeholder={
                  available === false
                    ? "Asisten belum aktif…"
                    : pendingSheet
                      ? "Mau diapakan filenya? mis. “isikan stok di kolom D”"
                      : "Tulis pertanyaan…"
                }
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
                // Ada lampiran menunggu → boleh kirim walau teks kosong.
                disabled={busy || (!input.trim() && !pendingSheet) || available === false}
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
              <span className="chat-kbd-hint">
                <span className="kbd">Enter</span> kirim · <span className="kbd">Shift+Enter</span> baris baru
              </span>
              <span>Seret file Excel ke area chat untuk melampirkan.</span>
              <span>Jawaban part paling akurat bila menyertakan nomor rangka (VIN).</span>
            </div>
          </div>
        </div>
      </div>
    </AppShell>
  );
}

// Gambar EXPLODED VIEW (tool gambar_exploded) — tampil INLINE & BESAR di jawaban
// (bukan strip thumbnail kecil, atas permintaan pemilik 2026-07-08). PNG dilayani
// /api/ai/excel/{id} (butuh auth) → fetch blob → objectURL utk <img>. Klik → lightbox.
// Batas selaras dgn backend (_MAX_EXPLODED_FIGURES).
const MAX_EXPLODED = 6;
function AiExplodedImages({ images }: { images?: AIExplodedImage[] }) {
  const [urls, setUrls] = useState<Record<string, string>>({});
  const [err, setErr] = useState(false);
  const [zoomId, setZoomId] = useState<string | null>(null);
  const key = (images || []).map((i) => i.id).join(",");
  useEffect(() => {
    const token = getToken();
    const list = (images || []).filter((i) => i.id).slice(0, MAX_EXPLODED);
    if (!token || list.length === 0) {
      setUrls({});
      return;
    }
    let alive = true;
    const made: string[] = [];
    (async () => {
      const out: Record<string, string> = {};
      for (const img of list) {
        try {
          const blob = await exportAiExcel(token, img.id);
          const u = URL.createObjectURL(blob);
          made.push(u);
          out[img.id] = u;
        } catch {
          if (alive) setErr(true);
        }
      }
      if (alive) setUrls(out);
    })();
    return () => {
      alive = false;
      made.forEach((u) => URL.revokeObjectURL(u));
    };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [key]);

  const list = (images || []).filter((i) => i.id).slice(0, MAX_EXPLODED);
  if (list.length === 0) return null;
  const zoomImg = zoomId ? list.find((i) => i.id === zoomId) : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, marginTop: 10 }}>
      {list.map((img) => (
        // Gambar BESAR: lebar penuh bubble (maks 560px), tinggi mengikuti isi.
        <figure key={img.id} style={{ margin: 0 }}>
          <button
            type="button"
            onClick={() => urls[img.id] && setZoomId(img.id)}
            title={urls[img.id] ? "Klik untuk memperbesar" : "Memuat…"}
            style={{
              display: "block",
              width: "100%",
              maxWidth: 560,
              border: "1px solid var(--ink-200)",
              borderRadius: 12,
              background: "#fff",
              cursor: urls[img.id] ? "zoom-in" : "default",
              overflow: "hidden",
              padding: 0,
            }}
          >
            <div style={{ width: "100%", minHeight: 220, display: "grid", placeItems: "center", padding: 10 }}>
              {urls[img.id] ? (
                // eslint-disable-next-line @next/next/no-img-element
                <img
                  src={urls[img.id]}
                  alt={`Exploded view ${img.pn || ""}`}
                  loading="lazy"
                  style={{ width: "100%", height: "auto", maxHeight: 460, objectFit: "contain", display: "block" }}
                />
              ) : (
                <span style={{ fontSize: 12, color: "var(--ink-400)" }}>
                  {err ? "Gagal memuat gambar" : "Memuat gambar…"}
                </span>
              )}
            </div>
          </button>
          <figcaption style={{ fontSize: 11.5, color: "var(--ink-500)", marginTop: 4, paddingLeft: 2 }}>
            {img.balon != null ? `Balon ${img.balon} · ` : ""}
            <span className="mono">{img.pn}</span>
            {img.nama_figure ? ` · ${img.nama_figure}` : ""}
          </figcaption>
        </figure>
      ))}
      {zoomImg && urls[zoomImg.id] && (
        <ImageLightbox
          src={urls[zoomImg.id]}
          onClose={() => setZoomId(null)}
          alt={`Exploded view ${zoomImg.pn || ""}`}
          caption={
            <>
              {zoomImg.balon != null ? `Balon ${zoomImg.balon} · ` : ""}
              <span className="mono">{zoomImg.pn}</span>
              {zoomImg.nama_figure ? ` · ${zoomImg.nama_figure}` : ""}
            </>
          }
        />
      )}
    </div>
  );
}

// Kartu file gaya Claude: ikon spreadsheet + nama file + tombol Unduh.
// Shell dipakai bersama ExcelCard (banding rangka) & AiExcelCard (export generik).
function ExcelCardShell({
  fname,
  sub,
  busy,
  err,
  onDownload,
  actionLabel = "Unduh",
}: {
  fname: string;
  sub: string;
  busy: boolean;
  err: string | null;
  onDownload: () => void;
  actionLabel?: string;
}) {
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
          <div style={{ fontSize: 11, color: "var(--ink-500)" }}>{sub}</div>
        </div>
        <button className="btn btn-secondary btn-sm" onClick={onDownload} disabled={busy} style={{ gap: 5 }}>
          <Icon d={IC.download} size={13} />
          {busy ? "Menyiapkan…" : actionLabel}
        </button>
      </div>
      {err && <div style={{ fontSize: 11.5, color: "var(--danger-600)", marginTop: 4 }}>{err}</div>}
    </div>
  );
}

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
    <ExcelCardShell fname={fname} sub={`Spreadsheet · XLSX${kat}`} busy={busy} err={err} onDownload={download} />
  );
}

// Kartu unduh Excel DINAMIS — file yang disusun asisten via tool buat_excel
// (mis. user bilang "buatkan excelnya" atas data yang barusan dibahas).
function AiExcelCard({ exp }: { exp: AIExcelExport }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);

  const isPdf = (exp.filename || "").toLowerCase().endsWith(".pdf");

  async function download() {
    const token = getToken();
    if (!token) return;
    setBusy(true);
    setErr(null);
    try {
      const blob = await exportAiExcel(token, exp.id);
      if (isPdf) {
        // PDF (lembar diagnosa SPN/FMI, penawaran, katalog) → BUKA di tab baru
        // agar langsung terbaca; popup diblokir → fallback unduh.
        const url = URL.createObjectURL(new Blob([blob], { type: "application/pdf" }));
        const w = window.open(url, "_blank", "noopener");
        if (!w) downloadBlob(blob, exp.filename || "Dokumen_MASPART.pdf");
        setTimeout(() => URL.revokeObjectURL(url), 60_000);
      } else {
        downloadBlob(blob, exp.filename || "Data_MASPART.xlsx");
      }
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) {
        setErr("File sudah kedaluwarsa — minta asisten buatkan lagi.");
      } else {
        setErr(e instanceof Error ? e.message : "Gagal membuka file");
      }
    } finally {
      setBusy(false);
    }
  }

  const sub = isPdf
    ? "PDF · klik untuk membuka"
    : `Spreadsheet · XLSX${exp.jumlah_baris ? ` · ${exp.jumlah_baris} baris` : ""}`;

  return (
    <ExcelCardShell
      fname={exp.judul || exp.filename}
      sub={sub}
      busy={busy}
      err={err}
      onDownload={download}
      actionLabel={isPdf ? "Buka" : "Unduh"}
    />
  );
}

// Kartu file yang MENUNGGU dikirim (gaya Claude): tampil di atas kotak ketik
// setelah user memilih file, sebelum ia mengetik maunya & menekan Kirim.
function FilePreviewCard({ name, onRemove }: { name: string; onRemove: () => void }) {
  const ext = (name.split(".").pop() || "").toUpperCase();
  return (
    <div style={{ marginBottom: 8 }}>
      <div
        style={{
          position: "relative",
          display: "flex",
          flexDirection: "column",
          justifyContent: "space-between",
          width: 150,
          height: 96,
          padding: "10px 10px 8px",
          border: "1px solid var(--ink-200)",
          borderRadius: 12,
          background: "var(--paper)",
          boxShadow: "var(--shadow-1)",
        }}
      >
        <div
          className="truncate"
          style={{ fontSize: 12, fontWeight: 600, color: "var(--ink-900)", paddingRight: 18 }}
          title={name}
        >
          {name}
        </div>
        <span
          className="pill"
          style={{ alignSelf: "flex-start", height: 19, fontSize: 10, padding: "0 7px", gap: 4 }}
        >
          <Icon d={IC.sheet} size={11} /> {ext}
        </span>
        <button
          onClick={onRemove}
          title="Buang lampiran"
          aria-label="Buang lampiran"
          style={{
            position: "absolute",
            top: -7,
            right: -7,
            width: 20,
            height: 20,
            borderRadius: 99,
            border: "1px solid var(--ink-200)",
            background: "var(--paper)",
            color: "var(--ink-600)",
            cursor: "pointer",
            display: "grid",
            placeItems: "center",
            padding: 0,
            boxShadow: "var(--shadow-1)",
          }}
        >
          <Icon d={IC.x} size={11} />
        </button>
      </div>
    </div>
  );
}

// Ringkasan Excel unggahan: kolom apa saja yang server kenali. Ditampilkan sekali
// di bawah jawaban pertama setelah file diunggah, agar user bisa langsung melihat
// kalau deteksi kolomnya meleset dan mengoreksinya lewat kalimat.
function SheetCard({ s }: { s: AISheetSummary }) {
  const dikenali = s.kolom.filter((k) => k.peran !== "lain");
  return (
    <div
      style={{
        marginTop: 8,
        maxWidth: 480,
        border: "1px solid var(--ink-200)",
        borderRadius: 12,
        background: "var(--paper)",
        padding: "10px 12px",
      }}
    >
      <div style={{ display: "flex", alignItems: "center", gap: 9, marginBottom: 8 }}>
        <span style={{ color: "var(--brand-700)", display: "inline-flex" }}>
          <Icon d={IC.sheet} size={16} />
        </span>
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="truncate" style={{ fontSize: 12.5, fontWeight: 600, color: "var(--ink-900)" }}>
            {s.filename}
          </div>
          <div style={{ fontSize: 11, color: "var(--ink-500)" }}>
            Sheet “{s.sheet}” · {s.jumlah_baris} baris × {s.jumlah_kolom} kolom
            {s.terpotong ? " (dipotong)" : ""}
          </div>
        </div>
      </div>
      <div style={{ display: "flex", flexWrap: "wrap", gap: 5 }}>
        {dikenali.length === 0 ? (
          <span style={{ fontSize: 11.5, color: "var(--ink-500)" }}>
            Tidak ada kolom yang dikenali otomatis — sebutkan kolom mana yang berisi Part Number.
          </span>
        ) : (
          dikenali.map((k) => (
            <span key={k.nama} className="pill" style={{ height: 21, fontSize: 10.5, padding: "0 8px" }}>
              {k.nama} → {PERAN_LABEL[k.peran] || k.peran}
            </span>
          ))
        )}
      </div>
      {s.kolom_part_number && (
        <div style={{ fontSize: 11, color: "var(--ink-500)", marginTop: 7 }}>
          {s.part_number_dikenal_di_katalog} dari {s.jumlah_baris} Part Number dikenal di katalog.
        </div>
      )}
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
      <div className="chat-bubble-in chat-row-user">
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
          {m.sheetName && (
            <div
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 7,
                background: "rgba(255,255,255,.18)",
                border: "1px solid rgba(255,255,255,.28)",
                borderRadius: 8,
                padding: "5px 9px",
                marginBottom: m.content ? 8 : 0,
                fontSize: 12.5,
                maxWidth: "100%",
              }}
            >
              <Icon d={IC.sheet} size={14} />
              <span className="truncate">{m.sheetName}</span>
            </div>
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
  // Streaming: jawaban belum ada, tampilkan STATUS langkah live.
  if (!m.content && m.streamStatus) {
    const steps = m.streamStatus;
    return (
      <div className="chat-bubble-in chat-row-ai">
        <div style={{ minWidth: 0, flex: 1 }}>
          <div className="chat-bubble-ai">
            {steps.length === 0 ? (
              <span style={{ color: "var(--ink-500)", fontSize: 13 }}>Memproses pertanyaan…</span>
            ) : (
              <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                {steps.map((s, i) => {
                  const last = i === steps.length - 1;
                  return (
                    <div
                      key={i}
                      style={{
                        fontSize: 13,
                        color: last ? "var(--ink-800)" : "var(--ink-400)",
                        display: "flex",
                        alignItems: "center",
                        gap: 7,
                      }}
                    >
                      <span>{last ? "⏳" : "✓"}</span>
                      <span>{s}</span>
                    </div>
                  );
                })}
              </div>
            )}
          </div>
        </div>
      </div>
    );
  }
  return (
    <div className="chat-bubble-in chat-row-ai">
      <div style={{ minWidth: 0, flex: 1 }}>
        <div className="chat-bubble-ai">
          <Markdown content={m.content} />
        </div>
        {m.sheet && <SheetCard s={m.sheet} />}
        {m.repairkitModels && m.repairkitModels.length > 0 && (
          <RepairKitDownloads models={m.repairkitModels} />
        )}
        {m.bandingExports?.map((exp, i) => (
          <ExcelCard key={i} exp={exp} />
        ))}
        {m.excelExports?.map((exp, i) => (
          <AiExcelCard key={exp.id || i} exp={exp} />
        ))}
        {/* Gambar exploded view → tampil BESAR & inline. Thumbnail foto part
            (PartThumbs) tetap dimatikan atas permintaan pemilik (2026-07-08). */}
        {m.explodedImages && m.explodedImages.length > 0 && (
          <AiExplodedImages images={m.explodedImages} />
        )}
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
