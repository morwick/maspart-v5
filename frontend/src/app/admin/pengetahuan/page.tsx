"use client";

import { Fragment, useCallback, useEffect, useRef, useState } from "react";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import {
  ApiError,
  addPengetahuan,
  cariPengetahuan,
  deletePengetahuan,
  getPengetahuan,
  getPengetahuanDetail,
  getPengetahuanStatus,
  reindexPengetahuan,
  updatePengetahuan,
  updatePengetahuanChunk,
  type PengetahuanChunk,
  type PengetahuanDok,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";

const EKSTENSI = ".pdf,.xlsx,.xlsm,.csv,.docx,.txt,.png,.jpg,.jpeg";
const POLL_MS = 2000;

const LABEL_STATUS: Record<PengetahuanDok["status"], string> = {
  antre: "Antre",
  proses: "Diproses",
  selesai: "Selesai",
  selesai_sebagian: "Selesai sebagian",
  gagal: "Gagal",
};

function warnaStatus(s: PengetahuanDok["status"]): string {
  if (s === "selesai") return "var(--brand-700)";
  if (s === "gagal") return "var(--danger-600)";
  if (s === "selesai_sebagian") return "var(--warn-700, #b45309)";
  return "var(--ink-500)";
}

function ukuran(b?: number): string {
  if (!b) return "";
  if (b < 1024) return `${b} B`;
  if (b < 1024 * 1024) return `${Math.round(b / 1024)} KB`;
  return `${(b / 1024 / 1024).toFixed(1)} MB`;
}

/** Teks tabel tempel-dari-Excel (TSV/CSV) → matriks. */
function parseTabel(raw: string): string[][] {
  return raw
    .split(/\r?\n/)
    .map((ln) => ln.trim())
    .filter(Boolean)
    .map((ln) => ln.split(/\t|;|,/).map((s) => s.trim()));
}

export default function PengetahuanPage() {
  const router = useRouter();
  const [docs, setDocs] = useState<PengetahuanDok[]>([]);
  const [jumlahChunk, setJumlahChunk] = useState(0);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);

  // Form tambah
  const [judul, setJudul] = useState("");
  const [deskripsi, setDeskripsi] = useState("");
  const [teks, setTeks] = useState("");
  const [tabelTxt, setTabelTxt] = useState("");
  const [tag, setTag] = useState("");
  const [untukPembeli, setUntukPembeli] = useState(false);
  const [pakaiAi, setPakaiAi] = useState(true);
  const [files, setFiles] = useState<File[]>([]);
  const [saving, setSaving] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  // Job berjalan (polling)
  const [jobId, setJobId] = useState("");

  // Kurasi chunk per dokumen
  const [expanded, setExpanded] = useState<string>("");
  const [chunks, setChunks] = useState<PengetahuanChunk[]>([]);
  const [busyId, setBusyId] = useState("");

  // Uji pencarian
  const [testQ, setTestQ] = useState("");
  const [testHasil, setTestHasil] = useState<PengetahuanChunk[] | null>(null);

  const load = useCallback(async () => {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoading(true);
    setError(null);
    try {
      const res = await getPengetahuan(token);
      setDocs(res.dokumen);
      setJumlahChunk(res.jumlah_chunk);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal memuat pengetahuan");
    } finally {
      setLoading(false);
    }
  }, [router]);

  useEffect(() => {
    if (!getToken()) {
      router.replace("/login");
      return;
    }
    if (getUser()?.role !== "admin") {
      router.replace("/search");
      return;
    }
    load();
  }, [router, load]);

  // Polling status job sampai selesai — indexing PDF besar bisa puluhan detik,
  // admin perlu melihat progresnya bergerak, bukan layar diam.
  useEffect(() => {
    if (!jobId) return;
    const token = getToken();
    if (!token) return;
    let hidup = true;
    const timer = setInterval(async () => {
      try {
        const s = await getPengetahuanStatus(token, jobId);
        if (!hidup) return;
        setDocs((prev) =>
          prev.map((d) => (d.id === jobId ? { ...d, ...s } : d)),
        );
        if (["selesai", "selesai_sebagian", "gagal"].includes(s.status)) {
          setJobId("");
          setNotice(
            s.status === "gagal"
              ? `Indexing gagal: ${s.error}`
              : `Indexing selesai — ${s.jumlah_chunk} bagian terindeks` +
                (s.error ? ` (dengan catatan: ${s.error})` : ""),
          );
          load();
        }
      } catch {
        setJobId(""); // dokumen mungkin sudah dihapus — hentikan polling
      }
    }, POLL_MS);
    return () => {
      hidup = false;
      clearInterval(timer);
    };
  }, [jobId, load]);

  function resetForm() {
    setJudul("");
    setDeskripsi("");
    setTeks("");
    setTabelTxt("");
    setTag("");
    setUntukPembeli(false);
    setPakaiAi(true);
    setFiles([]);
    if (fileRef.current) fileRef.current.value = "";
  }

  async function simpan() {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!judul.trim()) {
      setError("Judul wajib diisi.");
      return;
    }
    setSaving(true);
    setError(null);
    setNotice(null);
    try {
      const res = await addPengetahuan(token, {
        judul: judul.trim(),
        deskripsi,
        teks,
        tabel: tabelTxt.trim() ? parseTabel(tabelTxt) : undefined,
        tag,
        untuk_pembeli: untukPembeli,
        pakai_ai: pakaiAi,
        files,
      });
      resetForm();
      setNotice("Pengetahuan tersimpan — sedang diindeks di latar.");
      await load();
      setJobId(res.id);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan");
    } finally {
      setSaving(false);
    }
  }

  async function togglePublik(d: PengetahuanDok) {
    const token = getToken();
    if (!token) return;
    if (
      !d.untuk_pembeli &&
      !window.confirm(
        `Publikasikan "${d.judul}" ke PEMBELI?\n\nSeluruh isinya akan bisa ` +
          "dibaca akun pembeli lewat Asisten AI. Pastikan tidak ada data internal " +
          "(harga modal, stok gudang, catatan pribadi) di dalamnya.",
      )
    )
      return;
    setBusyId(d.id);
    try {
      await updatePengetahuan(token, d.id, { untuk_pembeli: !d.untuk_pembeli });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengubah");
    } finally {
      setBusyId("");
    }
  }

  async function toggleAktif(d: PengetahuanDok) {
    const token = getToken();
    if (!token) return;
    setBusyId(d.id);
    try {
      await updatePengetahuan(token, d.id, { aktif: !d.aktif });
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengubah");
    } finally {
      setBusyId("");
    }
  }

  async function hapus(d: PengetahuanDok) {
    const token = getToken();
    if (!token) return;
    if (!window.confirm(`Hapus "${d.judul}" beserta seluruh isi terindeksnya?`)) return;
    setBusyId(d.id);
    try {
      await deletePengetahuan(token, d.id);
      if (expanded === d.id) setExpanded("");
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menghapus");
    } finally {
      setBusyId("");
    }
  }

  async function reindex(d: PengetahuanDok) {
    const token = getToken();
    if (!token) return;
    setBusyId(d.id);
    try {
      await reindexPengetahuan(token, d.id);
      setJobId(d.id);
      setNotice(`"${d.judul}" diantre untuk diindeks ulang.`);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengantre");
    } finally {
      setBusyId("");
    }
  }

  async function reindexSemua() {
    const token = getToken();
    if (!token) return;
    const perlu = docs.filter((d) => d.perlu_reindex);
    if (!perlu.length) return;
    if (
      !window.confirm(
        `Indeks ulang ${perlu.length} dokumen berskema lama?\n\n` +
          "Kurasi manual (judul & kata kunci yang Anda perbaiki) dipertahankan. " +
          "Job berjalan satu per satu di latar, jadi bisa memakan waktu.",
      )
    )
      return;
    setBusyId("semua");
    try {
      // Antrian server berjalan SERIAL, jadi mengirim semuanya sekaligus aman
      // untuk RAM — tak ada dua dokumen besar diproses bersamaan.
      for (const d of perlu) await reindexPengetahuan(token, d.id);
      setNotice(`${perlu.length} dokumen diantre untuk diindeks ulang.`);
      setJobId(perlu[perlu.length - 1].id);
      await load();
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal mengantre");
    } finally {
      setBusyId("");
    }
  }

  async function buka(d: PengetahuanDok) {
    if (expanded === d.id) {
      setExpanded("");
      return;
    }
    const token = getToken();
    if (!token) return;
    setExpanded(d.id);
    setChunks([]);
    try {
      const res = await getPengetahuanDetail(token, d.id);
      setChunks(res.chunk);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal memuat bagian");
    }
  }

  async function simpanChunk(c: PengetahuanChunk, patch: {
    judul_id?: string;
    kata_kunci?: string[];
    dicari?: boolean;
  }) {
    const token = getToken();
    if (!token) return;
    const seq = c.id.split("#")[1] || "";
    setChunks((prev) => prev.map((x) => (x.id === c.id ? { ...x, ...patch } : x)));
    try {
      await updatePengetahuanChunk(token, c.dok_id, seq, patch);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menyimpan bagian");
      buka({ id: c.dok_id } as PengetahuanDok); // muat ulang bila gagal
    }
  }

  async function uji() {
    const token = getToken();
    if (!token || !testQ.trim()) return;
    try {
      const res = await cariPengetahuan(token, testQ.trim());
      setTestHasil(res.hasil);
    } catch (err) {
      setError(err instanceof Error ? err.message : "Gagal menguji pencarian");
    }
  }

  const jobDoc = docs.find((d) => d.id === jobId);

  return (
    <AppShell
      active="/admin/pengetahuan"
      title="Pengetahuan AI"
      sub="Isi yang diindeks & dipakai Asisten AI saat menjawab"
    >
      <div style={{ display: "grid", gap: 14, padding: 12 }}>
        {error && <div className="alert alert-error">{error}</div>}
        {notice && <div className="alert">{notice}</div>}

        {/* ── Form tambah ─────────────────────────────────────────── */}
        <div className="surface" style={{ padding: 14, display: "grid", gap: 10 }}>
          <div style={{ fontWeight: 700, fontSize: 14 }}>Tambah pengetahuan</div>
          <div style={{ fontSize: 12, color: "var(--ink-500)", lineHeight: 1.55 }}>
            Tulis langsung dan/atau lampirkan berkas (PDF, Excel, Word, CSV, TXT,
            gambar). Server membaca isinya — termasuk tabel dan gambar di dalamnya —
            lalu mengindeksnya supaya bisa ditemukan Asisten AI.
          </div>

          <input
            className="inp"
            placeholder="Judul — mis. Prosedur Retur Barang"
            value={judul}
            onChange={(e) => setJudul(e.target.value)}
          />
          <input
            className="inp"
            placeholder="Deskripsi singkat (opsional)"
            value={deskripsi}
            onChange={(e) => setDeskripsi(e.target.value)}
          />
          <textarea
            className="inp"
            rows={5}
            placeholder="Isi pengetahuan — tulis apa adanya, boleh panjang."
            value={teks}
            onChange={(e) => setTeks(e.target.value)}
          />
          <textarea
            className="inp"
            rows={3}
            placeholder="Tabel (opsional) — tempel dari Excel, satu baris per baris tabel."
            value={tabelTxt}
            onChange={(e) => setTabelTxt(e.target.value)}
          />
          <input
            className="inp"
            placeholder="Tag pencarian, pisahkan koma (opsional) — mis. retur, garansi"
            value={tag}
            onChange={(e) => setTag(e.target.value)}
          />

          <div>
            <input
              ref={fileRef}
              type="file"
              multiple
              accept={EKSTENSI}
              onChange={(e) => setFiles(Array.from(e.target.files || []))}
            />
            {files.length > 0 && (
              <div style={{ marginTop: 6, fontSize: 12, color: "var(--ink-600)" }}>
                {files.map((f) => (
                  <div key={f.name}>
                    {f.name} <span style={{ color: "var(--ink-400)" }}>{ukuran(f.size)}</span>
                  </div>
                ))}
              </div>
            )}
          </div>

          <div style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 13 }}>
            <label style={{ display: "flex", gap: 6, alignItems: "center" }}>
              <input
                type="checkbox"
                checked={pakaiAi}
                onChange={(e) => setPakaiAi(e.target.checked)}
              />
              Perkaya dengan AI (judul &amp; kata kunci Indonesia otomatis)
            </label>
            <label
              style={{
                display: "flex",
                gap: 6,
                alignItems: "center",
                color: untukPembeli ? "var(--danger-600)" : undefined,
              }}
            >
              <input
                type="checkbox"
                checked={untukPembeli}
                onChange={(e) => setUntukPembeli(e.target.checked)}
              />
              Boleh dibaca PEMBELI
            </label>
          </div>

          <div style={{ display: "flex", gap: 8 }}>
            <button className="btn btn-primary" onClick={simpan} disabled={saving}>
              {saving ? "Menyimpan…" : "Simpan & indeks"}
            </button>
            <button className="btn btn-ghost" onClick={resetForm} disabled={saving}>
              Bersihkan
            </button>
          </div>
        </div>

        {/* ── Progres job ─────────────────────────────────────────── */}
        {jobDoc && (
          <div className="surface" style={{ padding: 14, display: "grid", gap: 8 }}>
            <div style={{ fontWeight: 650, fontSize: 13 }}>
              Mengindeks “{jobDoc.judul}”
            </div>
            <div style={{ fontSize: 12, color: "var(--ink-600)" }}>
              {jobDoc.progres?.langkah || "Menyiapkan"}
              {jobDoc.progres?.total
                ? ` — ${jobDoc.progres.kini}/${jobDoc.progres.total}`
                : ""}
            </div>
            <div
              style={{
                height: 8,
                borderRadius: 99,
                background: "var(--ink-100)",
                overflow: "hidden",
              }}
            >
              <div
                style={{
                  width: `${jobDoc.progres?.persen || 5}%`,
                  height: "100%",
                  background: "var(--brand-600, var(--brand-700))",
                  transition: "width .3s",
                }}
              />
            </div>
          </div>
        )}

        {/* ── Uji pencarian ───────────────────────────────────────── */}
        <div className="surface" style={{ padding: 14, display: "grid", gap: 8 }}>
          <div style={{ fontWeight: 650, fontSize: 13 }}>Uji pencarian</div>
          <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
            Lihat persis apa yang ditemukan Asisten AI untuk sebuah pertanyaan.
          </div>
          <div style={{ display: "flex", gap: 8 }}>
            <input
              className="inp"
              style={{ flex: 1 }}
              placeholder="mis. cara retur barang"
              value={testQ}
              onChange={(e) => setTestQ(e.target.value)}
              onKeyDown={(e) => e.key === "Enter" && uji()}
            />
            <button className="btn btn-secondary" onClick={uji}>
              Cari
            </button>
          </div>
          {testHasil !== null && (
            <div style={{ fontSize: 12.5 }}>
              {testHasil.length === 0 ? (
                <div style={{ color: "var(--danger-600)" }}>
                  Tidak ada yang cocok — tambahkan kata kunci pada bagian terkait
                  supaya bisa ditemukan.
                </div>
              ) : (
                <ol style={{ paddingLeft: 18, display: "grid", gap: 4 }}>
                  {testHasil.map((h) => (
                    <li key={h.id}>
                      <b>{h.judul_id || h.judul}</b>{" "}
                      <span style={{ color: "var(--ink-500)" }}>
                        — {h.sumber}
                        {h.halaman ? ` hal ${h.halaman}` : ""}
                      </span>
                    </li>
                  ))}
                </ol>
              )}
            </div>
          )}
        </div>

        {/* ── Daftar dokumen ──────────────────────────────────────── */}
        <div className="surface" style={{ padding: 14 }}>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "center",
              marginBottom: 8,
            }}
          >
            <div style={{ fontWeight: 700, fontSize: 14 }}>
              Pengetahuan tersimpan ({docs.length})
            </div>
            <div style={{ display: "flex", gap: 10, alignItems: "center" }}>
              {docs.some((d) => d.perlu_reindex) && (
                <button
                  className="btn btn-secondary btn-sm"
                  onClick={reindexSemua}
                  disabled={!!busyId || !!jobId}
                  title="Ambil gambar di dalam berkas, breadcrumb bab, dan nama kolom tabel untuk dokumen berskema lama"
                >
                  Indeks ulang yang perlu (
                  {docs.filter((d) => d.perlu_reindex).length})
                </button>
              )}
              <div style={{ fontSize: 12, color: "var(--ink-500)" }}>
                {jumlahChunk} bagian terindeks
              </div>
            </div>
          </div>

          {loading ? (
            <div style={{ fontSize: 13, color: "var(--ink-500)" }}>Memuat…</div>
          ) : docs.length === 0 ? (
            <div style={{ fontSize: 13, color: "var(--ink-500)" }}>
              Belum ada. Tambahkan lewat form di atas.
            </div>
          ) : (
            <table className="tbl">
              <thead>
                <tr>
                  <th>Judul</th>
                  <th>Sumber</th>
                  <th>Bagian</th>
                  <th>Pengayaan</th>
                  <th>Pembeli</th>
                  <th>Status</th>
                  <th />
                </tr>
              </thead>
              <tbody>
                {docs.map((d) => (
                  <Fragment key={d.id}>
                    <tr style={{ opacity: d.aktif ? 1 : 0.55 }}>
                      <td>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => buka(d)}
                          style={{ padding: 0, fontWeight: 650 }}
                        >
                          {expanded === d.id ? "▾" : "▸"} {d.judul}
                        </button>
                        {d.deskripsi && (
                          <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>
                            {d.deskripsi}
                          </div>
                        )}
                      </td>
                      <td style={{ fontSize: 12 }}>
                        {d.berkas?.length
                          ? d.berkas.map((b) => b.nama).join(", ")
                          : "diketik admin"}
                      </td>
                      <td>{d.jumlah_chunk}</td>
                      <td style={{ fontSize: 12 }}>
                        {d.pengayaan === "llm"
                          ? "AI"
                          : d.pengayaan === "campuran"
                            ? "AI sebagian"
                            : d.pengayaan
                              ? "otomatis"
                              : "—"}
                      </td>
                      <td>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => togglePublik(d)}
                          disabled={busyId === d.id}
                          style={{
                            color: d.untuk_pembeli ? "var(--danger-600)" : "var(--ink-500)",
                          }}
                          title={
                            d.untuk_pembeli
                              ? "Terbuka untuk pembeli — klik untuk menutup"
                              : "Internal — klik untuk mempublikasikan"
                          }
                        >
                          {d.untuk_pembeli ? "Publik" : "Internal"}
                        </button>
                      </td>
                      <td style={{ fontSize: 12, color: warnaStatus(d.status) }}>
                        {LABEL_STATUS[d.status] || d.status}
                        {d.perlu_reindex && (
                          <div
                            style={{ fontSize: 11, color: "var(--warn-700, #b45309)" }}
                            title="Diindeks sebelum pembedahan dokumen ditingkatkan — indeks ulang untuk mengambil gambar di dalam berkas, breadcrumb bab, dan nama kolom tabel."
                          >
                            skema lama — indeks ulang
                          </div>
                        )}
                        {d.error && (
                          <div
                            style={{
                              fontSize: 11,
                              color: "var(--ink-500)",
                              maxWidth: 260,
                            }}
                          >
                            {d.error}
                          </div>
                        )}
                      </td>
                      <td style={{ whiteSpace: "nowrap" }}>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => reindex(d)}
                          disabled={busyId === d.id}
                        >
                          Indeks ulang
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => toggleAktif(d)}
                          disabled={busyId === d.id}
                        >
                          {d.aktif ? "Nonaktifkan" : "Aktifkan"}
                        </button>
                        <button
                          className="btn btn-ghost btn-sm"
                          onClick={() => hapus(d)}
                          disabled={busyId === d.id}
                          style={{ color: "var(--danger-600)" }}
                        >
                          Hapus
                        </button>
                      </td>
                    </tr>
                    {expanded === d.id && (
                      <tr>
                        <td colSpan={7} style={{ background: "var(--canvas)" }}>
                          {chunks.length === 0 ? (
                            <div style={{ fontSize: 12, padding: 8 }}>
                              Belum ada bagian terindeks.
                            </div>
                          ) : (
                            <div style={{ display: "grid", gap: 10, padding: 8 }}>
                              <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>
                                Perbaiki judul &amp; kata kunci di sini bila hasil
                                pencarian kurang tepat — inilah yang dipakai untuk
                                mencocokkan pertanyaan user.
                              </div>
                              {chunks.map((c) => (
                                <div
                                  key={c.id}
                                  className="surface"
                                  style={{ padding: 10, display: "grid", gap: 6 }}
                                >
                                  <div style={{ fontSize: 11, color: "var(--ink-400)" }}>
                                    {c.sumber}
                                    {c.halaman ? ` · hal ${c.halaman}` : ""} · {c.tipe}
                                    {c.bahasa && c.bahasa !== "id"
                                      ? ` · bahasa ${c.bahasa}`
                                      : ""}
                                    {c.kurasi ? " · dikurasi admin" : ""}
                                  </div>
                                  {/* Hasil ekstraksi V2 — supaya admin bisa
                                      MELIHAT apakah pembedahan dokumen berhasil,
                                      bukan menebak dari hasil pencarian saja. */}
                                  {(c.jalur?.length || c.kolom?.length) && (
                                    <div style={{ fontSize: 11, color: "var(--ink-500)" }}>
                                      {c.jalur?.length ? c.jalur.join(" › ") : ""}
                                      {c.kolom?.length
                                        ? `${c.jalur?.length ? " · " : ""}kolom: ${c.kolom.join(", ")}`
                                        : ""}
                                      {c.baris_total
                                        ? ` · ${c.baris_total} baris`
                                        : ""}
                                    </div>
                                  )}
                                  <input
                                    className="inp"
                                    value={c.judul_id}
                                    onChange={(e) =>
                                      setChunks((prev) =>
                                        prev.map((x) =>
                                          x.id === c.id
                                            ? { ...x, judul_id: e.target.value }
                                            : x,
                                        ),
                                      )
                                    }
                                    onBlur={(e) =>
                                      simpanChunk(c, { judul_id: e.target.value })
                                    }
                                  />
                                  <input
                                    className="inp"
                                    placeholder="kata kunci, pisahkan koma"
                                    value={(c.kata_kunci || []).join(", ")}
                                    onChange={(e) =>
                                      setChunks((prev) =>
                                        prev.map((x) =>
                                          x.id === c.id
                                            ? {
                                                ...x,
                                                kata_kunci: e.target.value.split(","),
                                              }
                                            : x,
                                        ),
                                      )
                                    }
                                    onBlur={(e) =>
                                      simpanChunk(c, {
                                        kata_kunci: e.target.value
                                          .split(",")
                                          .map((s) => s.trim())
                                          .filter(Boolean),
                                      })
                                    }
                                  />
                                  <label
                                    style={{
                                      fontSize: 12,
                                      display: "flex",
                                      gap: 6,
                                      alignItems: "center",
                                    }}
                                  >
                                    <input
                                      type="checkbox"
                                      checked={c.dicari}
                                      onChange={(e) =>
                                        simpanChunk(c, { dicari: e.target.checked })
                                      }
                                    />
                                    Ikut dicari asisten
                                  </label>
                                  {c.ringkasan && (
                                    <div
                                      style={{ fontSize: 12, color: "var(--ink-600)" }}
                                    >
                                      {c.ringkasan}
                                    </div>
                                  )}
                                  {c.teks && (
                                    <div
                                      style={{
                                        fontSize: 11.5,
                                        color: "var(--ink-500)",
                                        whiteSpace: "pre-wrap",
                                        maxHeight: 90,
                                        overflow: "hidden",
                                      }}
                                    >
                                      {c.teks.slice(0, 400)}
                                    </div>
                                  )}
                                  {c.gambar_ref?.length > 0 && (
                                    <div style={{ fontSize: 11, color: "var(--ink-400)" }}>
                                      {c.gambar_ref.length} gambar
                                      {c.gambar_info?.some((g) => g.caption)
                                        ? ` · ${c.gambar_info
                                            .filter((g) => g.caption)
                                            .map((g) => g.caption)
                                            .join(" | ")
                                            .slice(0, 160)}`
                                        : " · tanpa keterangan"}
                                    </div>
                                  )}
                                </div>
                              ))}
                            </div>
                          )}
                        </td>
                      </tr>
                    )}
                  </Fragment>
                ))}
              </tbody>
            </table>
          )}
        </div>
      </div>
    </AppShell>
  );
}
