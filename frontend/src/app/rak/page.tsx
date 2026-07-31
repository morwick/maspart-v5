"use client";

import { useCallback, useEffect, useRef, useState } from "react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import AppShell from "@/components/AppShell";
import ImageLightbox from "@/components/ImageLightbox";
import {
  ApiError,
  deleteRak,
  getAdminGudang,
  importRak,
  listRakGudang,
  partImageUrl,
  saveRak,
  uploadRakFoto,
  type RakInfo,
} from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { ensurePerms } from "@/lib/perms";
import { EmptyIcon, EmptyState } from "@/components/States";

/** Tanggal ringkas — kolom "diperbarui" tak perlu jam. */
function tglSingkat(iso: string): string {
  if (!iso) return "";
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleDateString("id-ID", { day: "2-digit", month: "short", year: "numeric" });
}

export default function RakPage() {
  const router = useRouter();
  // Daftar gudang yang boleh DITULIS. Admin melihat semua gudang (dari config +
  // indeks Accurate), staf hanya yang ditugaskan padanya (users.gudang_kelola).
  const [opsi, setOpsi] = useState<string[]>([]);
  const [gudang, setGudang] = useState("");
  const [items, setItems] = useState<RakInfo[]>([]);
  const [qInput, setQInput] = useState("");
  const [q, setQ] = useState("");
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [msg, setMsg] = useState<string | null>(null);
  const [lightbox, setLightbox] = useState<string | null>(null);
  const [editPn, setEditPn] = useState<string | null>(null);
  // Impor Excel
  const fileRef = useRef<HTMLInputElement>(null);
  const [imporBusy, setImporBusy] = useState(false);
  const [dilewati, setDilewati] = useState<{ pn: string; alasan: string }[]>([]);

  const fail = useCallback(
    (e: unknown) => {
      if (e instanceof ApiError && e.status === 401) {
        clearSession();
        router.replace("/login");
        return;
      }
      setError(e instanceof Error ? e.message : "Gagal");
      setMsg(null);
    },
    [router],
  );

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    if (getUser()?.role === "pembeli") {
      router.replace("/toko");
      return;
    }
    const admin = getUser()?.role === "admin";
    let alive = true;
    (async () => {
      let labels: string[] = [];
      if (admin) {
        try {
          const d = await getAdminGudang(token);
          labels = d.gudang.map((g) => g.label);
        } catch {
          labels = [];
        }
      }
      if (!labels.length) {
        // Staf gudang (dan admin bila daftar gudang gagal dimuat) memakai daftar
        // dari payload izin — itu pula yang dipagari backend saat menulis.
        const p = await ensurePerms();
        labels = p?.gudang_kelola ?? [];
      }
      if (!alive) return;
      setOpsi(labels);
      setGudang((g) => g || labels[0] || "");
    })();
    return () => {
      alive = false;
    };
  }, [router]);

  const load = useCallback(
    async (label: string, keyword: string) => {
      const token = getToken();
      if (!token || !label) return;
      setLoading(true);
      setError(null);
      try {
        const d = await listRakGudang(token, label, keyword);
        setItems(d.items);
        setEditPn(null);
      } catch (e) {
        fail(e);
      } finally {
        setLoading(false);
      }
    },
    [fail],
  );

  useEffect(() => {
    if (gudang) void load(gudang, q);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [gudang]);

  async function hapus(row: RakInfo) {
    const token = getToken();
    if (!token) return;
    if (!window.confirm(`Hapus data rak ${row.part_number} di ${gudang}?`)) return;
    try {
      await deleteRak(token, gudang, row.part_number);
      setMsg(`Rak ${row.part_number} dihapus.`);
      await load(gudang, q);
    } catch (e) {
      fail(e);
    }
  }

  async function impor() {
    const token = getToken();
    const f = fileRef.current?.files?.[0];
    if (!token || !f || !gudang) return;
    setImporBusy(true);
    setError(null);
    setDilewati([]);
    try {
      const r = await importRak(token, gudang, f);
      setMsg(
        `${r.tersimpan} baris tersimpan ke ${r.gudang}` +
          (r.jumlah_dilewati ? ` · ${r.jumlah_dilewati} baris dilewati.` : "."),
      );
      setDilewati(r.dilewati || []);
      if (fileRef.current) fileRef.current.value = "";
      await load(gudang, q);
    } catch (e) {
      fail(e);
    } finally {
      setImporBusy(false);
    }
  }

  return (
    <AppShell
      active="/rak"
      title="Rak & Kartu Stok"
      sub="Di mana barangnya — lokasi rak per gudang + foto kartu stok"
    >
      <div className="mx-auto w-full max-w-5xl px-4 py-6 sm:px-7">
        {/* Pemilih gudang + pencarian */}
        <div className="flex flex-wrap gap-2">
          <select
            className="select"
            style={{ width: "auto", minWidth: 190 }}
            value={gudang}
            onChange={(e) => setGudang(e.target.value)}
          >
            {opsi.length === 0 && <option value="">(tak ada gudang)</option>}
            {opsi.map((g) => (
              <option key={g} value={g}>{g}</option>
            ))}
          </select>
          <form
            onSubmit={(e) => {
              e.preventDefault();
              setQ(qInput);
              void load(gudang, qInput);
            }}
            className="flex flex-1 gap-2"
            style={{ minWidth: 240 }}
          >
            <input
              className="input mono"
              value={qInput}
              onChange={(e) => setQInput(e.target.value)}
              placeholder="Cari Part Number / kode rak / catatan…"
            />
            <button className="btn btn-primary" disabled={!gudang}>Cari</button>
          </form>
        </div>

        {opsi.length === 0 && (
          <div className="alert" style={{ marginTop: 16, background: "var(--warn-50)", color: "var(--warn-600)", borderColor: "#f6d9a8" }}>
            Akun ini belum ditugaskan mengelola gudang mana pun. Minta admin
            menambahkannya di <b>Manajemen User → Gudang Kelola</b>.
          </div>
        )}

        {error && <div className="alert alert-error" style={{ marginTop: 12 }}>{error}</div>}
        {msg && (
          <div className="alert" style={{ marginTop: 12, background: "var(--brand-50, #eef8f0)", color: "var(--brand-700)", borderColor: "var(--brand-600)" }}>
            {msg}
          </div>
        )}

        {/* Unggah massal */}
        <div className="surface surface-pad" style={{ marginTop: 14 }}>
          <div style={{ fontSize: 13, fontWeight: 600, marginBottom: 6 }}>Unggah daftar rak (Excel/CSV)</div>
          <div style={{ fontSize: 12, color: "var(--ink-500)", lineHeight: 1.6, marginBottom: 8 }}>
            Satu file = satu gudang (<b>{gudang || "—"}</b>) — hak tulis dipagari per
            gudang, jadi file campur-gudang tak bisa diperiksa per baris. Kolom yang
            dibaca: <b>Part Number</b> | <b>Rak</b> | <b>Catatan</b> (nama kolom boleh
            bervariasi: &quot;Kode&quot;/&quot;No Part&quot;, &quot;Lokasi&quot;,
            &quot;Keterangan&quot;). Baris tanpa kode rak dilewati dan dilaporkan.
            Format .xlsx/.xls/.xlsm/.csv, maks 10 MB.
          </div>
          <div className="flex flex-wrap items-center gap-2">
            <input ref={fileRef} type="file" accept=".xlsx,.xls,.xlsm,.csv" style={{ fontSize: 12 }} />
            <button className="btn btn-secondary btn-sm" onClick={impor} disabled={imporBusy || !gudang}>
              {imporBusy ? "Mengunggah…" : "⬆ Unggah"}
            </button>
          </div>
          {dilewati.length > 0 && (
            <div style={{ marginTop: 10, fontSize: 12, color: "var(--ink-600)" }}>
              <div style={{ fontWeight: 600, marginBottom: 4 }}>Baris dilewati:</div>
              <ul style={{ maxHeight: 160, overflow: "auto", paddingLeft: 16, listStyle: "disc" }}>
                {dilewati.map((d, i) => (
                  <li key={`${d.pn}-${i}`}>
                    <span className="mono">{d.pn || "(kosong)"}</span> — {d.alasan}
                  </li>
                ))}
              </ul>
            </div>
          )}
        </div>

        {/* Daftar isi gudang */}
        {loading ? (
          <div className="surface grid place-items-center" style={{ marginTop: 14, height: 140, color: "var(--ink-500)", fontSize: 13 }}>
            Memuat data rak…
          </div>
        ) : items.length > 0 ? (
          <div className="surface" style={{ marginTop: 14, overflow: "auto" }}>
            <table className="tbl">
              <thead>
                <tr>
                  <th>Part Number</th>
                  <th>Rak</th>
                  <th>Catatan</th>
                  <th>Kartu</th>
                  <th>Diperbarui</th>
                  <th style={{ textAlign: "right" }}>Aksi</th>
                </tr>
              </thead>
              <tbody>
                {items.map((row) => (
                  <BarisRak
                    key={`${row.pn_key}-${row.gudang}`}
                    row={row}
                    gudang={gudang}
                    edit={editPn === row.part_number}
                    onEdit={() => setEditPn((p) => (p === row.part_number ? null : row.part_number))}
                    onSaved={async () => {
                      setEditPn(null);
                      await load(gudang, q);
                    }}
                    onHapus={() => hapus(row)}
                    onZoom={setLightbox}
                    onError={fail}
                  />
                ))}
              </tbody>
            </table>
          </div>
        ) : gudang ? (
          <div className="surface" style={{ marginTop: 14 }}>
            <EmptyState
              icon={EmptyIcon.box}
              title="Belum ada data rak"
              sub={
                q ? (
                  <>Tidak ada baris yang cocok dengan <b>{q}</b> di {gudang}.</>
                ) : (
                  <>Gudang {gudang} belum punya catatan rak. Unggah Excel di atas, atau isi
                  satu-satu dari halaman detail part (tabel Stok per Gudang → klik baris).</>
                )
              }
            />
          </div>
        ) : null}

        {items.length > 0 && (
          <p style={{ marginTop: 8, fontSize: 12, color: "var(--ink-400)" }}>
            {items.length.toLocaleString("id-ID")} baris · klik Part Number untuk membuka detail part.
          </p>
        )}
      </div>

      {lightbox && (
        <ImageLightbox src={partImageUrl(lightbox)} onClose={() => setLightbox(null)} alt="Kartu stok" />
      )}
    </AppShell>
  );
}

/** Satu baris rak — mode baca, atau form ubah inline (rak/catatan/foto). */
function BarisRak({
  row,
  gudang,
  edit,
  onEdit,
  onSaved,
  onHapus,
  onZoom,
  onError,
}: {
  row: RakInfo;
  gudang: string;
  edit: boolean;
  onEdit: () => void;
  onSaved: () => Promise<void> | void;
  onHapus: () => void;
  onZoom: (url: string) => void;
  onError: (e: unknown) => void;
}) {
  const [rak, setRak] = useState(row.rak);
  const [catatan, setCatatan] = useState(row.catatan);
  const [file, setFile] = useState<File | null>(null);
  const [busy, setBusy] = useState(false);

  // Baris dimuat ulang sesudah simpan/impor → isian ikut data terbaru.
  useEffect(() => {
    if (!edit) {
      setRak(row.rak);
      setCatatan(row.catatan);
      setFile(null);
    }
  }, [row.rak, row.catatan, edit]);

  async function simpan() {
    const token = getToken();
    if (!token) return;
    if (!rak.trim()) {
      onError(new Error("Kode rak wajib diisi."));
      return;
    }
    setBusy(true);
    try {
      await saveRak(token, gudang, row.part_number, { rak: rak.trim(), catatan: catatan.trim() });
      if (file) await uploadRakFoto(token, gudang, row.part_number, file);
      await onSaved();
    } catch (e) {
      onError(e);
    } finally {
      setBusy(false);
    }
  }

  if (edit) {
    return (
      <tr>
        <td className="pn">{row.part_number}</td>
        <td colSpan={4}>
          <div className="flex flex-wrap items-center gap-2">
            <input
              className="input mono"
              style={{ height: 32, width: 150 }}
              value={rak}
              onChange={(e) => setRak(e.target.value)}
              placeholder="Kode rak"
            />
            <input
              className="input"
              style={{ height: 32, flex: "1 1 180px", minWidth: 140 }}
              value={catatan}
              onChange={(e) => setCatatan(e.target.value)}
              placeholder="Catatan (opsional)"
            />
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              onChange={(e) => setFile(e.target.files?.[0] ?? null)}
              style={{ fontSize: 11.5, maxWidth: 190 }}
              title="Ganti foto kartu stok (jpg/png/webp, maks 10 MB)"
            />
          </div>
        </td>
        <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
          <button className="btn btn-primary btn-sm" onClick={simpan} disabled={busy}>
            {busy ? "…" : "Simpan"}
          </button>{" "}
          <button className="btn btn-secondary btn-sm" onClick={onEdit} disabled={busy}>Batal</button>
        </td>
      </tr>
    );
  }

  return (
    <tr>
      <td className="pn">
        <Link href={`/part/${encodeURIComponent(row.part_number)}`} style={{ color: "var(--brand-700)" }}>
          {row.part_number}
        </Link>
      </td>
      <td className="mono" style={{ fontWeight: 550 }}>{row.rak || "—"}</td>
      <td style={{ color: "var(--ink-600)" }}>{row.catatan || ""}</td>
      <td>
        {row.foto_url ? (
          <button
            type="button"
            onClick={() => onZoom(row.foto_url)}
            style={{ padding: 0, border: "1px solid var(--ink-200)", borderRadius: 6, overflow: "hidden", background: "var(--paper)", cursor: "zoom-in" }}
            title="Klik untuk perbesar kartu stok"
          >
            {/* eslint-disable-next-line @next/next/no-img-element */}
            <img
              src={partImageUrl(row.foto_url)}
              alt={`Kartu stok ${row.part_number}`}
              loading="lazy"
              style={{ width: 44, height: 44, objectFit: "cover", display: "block" }}
            />
          </button>
        ) : (
          <span style={{ color: "var(--ink-400)", fontSize: 12 }}>—</span>
        )}
      </td>
      <td style={{ color: "var(--ink-500)", fontSize: 12 }}>
        {row.updated_by || "—"}
        {row.updated_at ? ` · ${tglSingkat(row.updated_at)}` : ""}
      </td>
      <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
        <button className="btn btn-secondary btn-sm" onClick={onEdit}>Ubah</button>{" "}
        <button className="btn btn-secondary btn-sm" onClick={onHapus}>Hapus</button>
      </td>
    </tr>
  );
}
