/**
 * Parser TABEL Markdown (GFM) untuk jawaban Asisten AI — MURNI, tanpa JSX &
 * tanpa dependency (properti nol-dependency renderer chat sengaja dijaga).
 *
 * ⚠️ PARITAS: file ini adalah CERMIN dari `D:\src\maspart_mobile\lib\widgets\
 * md_table.dart`. Keduanya mengikuti spesifikasi yang sama persis di bawah;
 * kalau salah satu diubah, ubah yang lain di giliran yang sama (aturan paritas
 * web ↔ Flutter) dan pakai string fixture yang identik di test masing-masing.
 *
 * SPESIFIKASI BERSAMA
 * 1. splitCells: buang '|' terdepan/terbelakang, pecah pada '|' yang TIDAK
 *    didahului '\', trim, lalu buang sel kosong di EKOR (artefak pipa penutup).
 * 2. isSepRow: >=2 sel DAN tiap sel cocok /^:?-+:?$/. Ambang ">=2" itulah yang
 *    menjaga '---' polos tetap jadi <hr>, bukan tabel.
 * 3. Tabel MULAI di baris i bila: lines[i] MENGANDUNG '|' (tak harus diawali
 *    '|' — GFM mengizinkannya, dan itu kasus "tabel rusak" yang paling sering
 *    terlihat), isSepRow(lines[i+1]), dan jumlah kolom header == separator.
 * 4. Baris isi: lanjut selama baris tidak kosong, mengandung '|', bukan sepRow,
 *    dan tidak diawali '#'.
 * 5. Alignment GFM: ':---:'→center, '---:'→right, ':---'→left, '---'→null
 *    (serahkan ke heuristik).
 * 6. Heuristik kolom ANGKA (hanya bila alignment null) — per KOLOM, hanya sel
 *    body. Netral (tak ikut memilih): '', '-', '—', '–', 'n/a'. Kolom numerik
 *    bila >=1 sel numerik DAN 0 sel non-numerik. Kolom semua-netral → numerik
 *    bila judulnya kolom angka.
 * 7. Kolom PN = keputusan KOLOM, bukan per sel (akar bug "satu kolom dua
 *    font"): judul cocok, atau >=60% sel non-kosong pnLike DAN minimal satu sel
 *    mengandung huruf. Kolom numerik tak pernah jadi kolom PN.
 * 8. Normalisasi: nCols = max(header, baris terpanjang); semua dipadatkan —
 *    tak ada sel yang dibuang diam-diam.
 * 9. Parser WAJIB TOTAL: tak boleh melempar untuk input separuh jadi apa pun
 *    (draf streaming melewatinya tiap token).
 */

export type MdAlign = "left" | "right" | "center";

export type MdTable = {
  header: string[];
  rows: string[][];
  /** Alignment eksplisit dari baris pemisah GFM (null = tak disebut model). */
  aligns: (MdAlign | null)[];
  /** Kolom yang disajikan rata kanan + tabular-nums. */
  numCols: Set<number>;
  /** Kolom Part Number (font mono, tak dipotong) — null bila tak ada. */
  pnCol: number | null;
  nCols: number;
};

/** Ambang kolom: >= ini, tabel berubah jadi kartu-per-baris di layar sempit. */
export const MD_CARD_MIN_COLS = 4;

const SEP_CELL = /^:?-+:?$/;

// Sel yang TIDAK ikut memilih jenis kolom. '—' berarti "belum ada data"
// (aturan prompt), bukan angka dan bukan teks — ia tak boleh menggugurkan
// sebuah kolom angka hanya karena satu barisnya kosong.
const NEUTRAL = new Set(["", "-", "—", "–", "n/a"]);

const NUM_PLAIN = /^[+-]?\d+([.,]\d+)?$/;
const NUM_THOUSAND = /^(rp\s*)?[+-]?\d{1,3}([.\s]\d{3})+(,\d+)?$/i;
const NUM_RUPIAH = /^rp\s*[+-]?\d[\d.,\s]*$/i;
const NUM_UNIT =
  /^[+-]?\d[\d.,\s]*\s*(pcs|pc|unit|set|ea|kg|gr|g|mm|cm|m|liter|ltr|l|%|x|jam|hari|bh|buah|lembar|pasang)$/i;

const HEADER_NUM = /^(stok|qty|jumlah|harga|price|total|berat|nilai|subtotal|sisa)/i;
const HEADER_PN = /(part\s*number|part\s*no\b|^pn$|^p\/n$|nomor\s*part)/i;

/** Buang penanda inline supaya heuristik menilai ISI, bukan gaya tulisnya. */
function strip(s: string): string {
  return (s || "").replace(/\*\*/g, "").replace(/[*`]/g, "").trim();
}

export function splitCells(line: string): string[] {
  let s = (line || "").trim();
  if (s.startsWith("|")) s = s.slice(1);
  if (s.endsWith("|") && !s.endsWith("\\|")) s = s.slice(0, -1);
  const out: string[] = [];
  let cur = "";
  for (let k = 0; k < s.length; k++) {
    const ch = s[k];
    if (ch === "\\" && s[k + 1] === "|") {
      cur += "|";
      k++;
      continue;
    }
    if (ch === "|") {
      out.push(cur.trim());
      cur = "";
      continue;
    }
    cur += ch;
  }
  out.push(cur.trim());
  while (out.length > 1 && out[out.length - 1] === "") out.pop();
  return out;
}

export function isSepRow(line: string): boolean {
  if (!line || !line.includes("-")) return false;
  const cells = splitCells(line);
  return cells.length >= 2 && cells.every((c) => SEP_CELL.test(c));
}

function alignOf(sepCell: string): MdAlign | null {
  const kiri = sepCell.startsWith(":");
  const kanan = sepCell.endsWith(":");
  if (kiri && kanan) return "center";
  if (kanan) return "right";
  if (kiri) return "left";
  return null;
}

type Jenis = "neutral" | "num" | "other";

function jenisSel(raw: string): Jenis {
  const s = strip(raw);
  if (NEUTRAL.has(s.toLowerCase())) return "neutral";
  if (NUM_PLAIN.test(s) || NUM_THOUSAND.test(s) || NUM_RUPIAH.test(s) || NUM_UNIT.test(s)) {
    return "num";
  }
  return "other";
}

/** PN selalu memuat ANGKA — syarat itu yang menjaga nama part beruruf besar
 *  ('HANDLE') tidak ikut dianggap Part Number. Suffix varian ('/1', '+003/1')
 *  sudah tercakup kelas karakternya. */
function isPnLike(raw: string): boolean {
  const s = strip(raw);
  if (s.length < 5 || s.length > 32) return false;
  if (!/^[A-Z0-9][A-Z0-9./+\-]*$/.test(s)) return false;
  if (!/\d/.test(s)) return false;
  if (/^\d{1,3}([.,]\d{3})+$/.test(s)) return false; // 1.250.000
  if (/^RP/i.test(s)) return false;
  return true;
}

function hitungNumCols(
  header: string[],
  rows: string[][],
  aligns: (MdAlign | null)[],
  nCols: number,
): Set<number> {
  const out = new Set<number>();
  for (let ci = 0; ci < nCols; ci++) {
    const a = aligns[ci];
    if (a === "right") {
      out.add(ci);
      continue;
    }
    if (a === "center" || a === "left") continue; // eksplisit dari model — hormati
    let num = 0;
    let other = 0;
    let ada = 0;
    for (const r of rows) {
      const j = jenisSel(r[ci] ?? "");
      if (j === "neutral") continue;
      ada++;
      if (j === "num") num++;
      else other++;
    }
    if (ada === 0) {
      if (HEADER_NUM.test(strip(header[ci] ?? ""))) out.add(ci);
      continue;
    }
    if (num >= 1 && other === 0) out.add(ci);
  }
  return out;
}

function cariPnCol(
  header: string[],
  rows: string[][],
  numCols: Set<number>,
  nCols: number,
): number | null {
  for (let ci = 0; ci < nCols; ci++) {
    if (numCols.has(ci)) continue;
    if (HEADER_PN.test(strip(header[ci] ?? ""))) return ci;
  }
  for (let ci = 0; ci < nCols; ci++) {
    if (numCols.has(ci)) continue;
    let isi = 0;
    let pn = 0;
    let berhuruf = false;
    for (const r of rows) {
      const s = strip(r[ci] ?? "");
      if (!s) continue;
      isi++;
      if (isPnLike(s)) pn++;
      if (/[A-Z]/.test(s)) berhuruf = true;
    }
    // 'berhuruf' diperiksa di level KOLOM: PN murni-angka (190003962518) tetap
    // dapat font mono karena bertetangga dgn WG9925520270, sedangkan kolom Stok
    // yang seluruhnya angka tak pernah jadi kolom PN.
    if (isi > 0 && berhuruf && pn / isi >= 0.6) return ci;
  }
  return null;
}

/** Parse tabel yang MULAI di `lines[i]`. null bila di situ bukan tabel. */
export function parseTableAt(
  lines: string[],
  i: number,
): { table: MdTable; next: number } | null {
  const head = lines[i] ?? "";
  if (!head.includes("|")) return null;
  if (i + 1 >= lines.length || !isSepRow(lines[i + 1])) return null;

  const header = splitCells(head);
  const sep = splitCells(lines[i + 1]);
  if (sep.length < 2 || header.length !== sep.length) return null;

  const rows: string[][] = [];
  let j = i + 2;
  while (j < lines.length) {
    const t = (lines[j] ?? "").trim();
    if (!t || !t.includes("|") || t.startsWith("#") || isSepRow(lines[j])) break;
    rows.push(splitCells(lines[j]));
    j++;
  }

  let nCols = header.length;
  for (const r of rows) if (r.length > nCols) nCols = r.length;
  const pad = (a: string[]) => {
    const out = a.slice(0, nCols);
    while (out.length < nCols) out.push("");
    return out;
  };
  const H = pad(header);
  const R = rows.map(pad);
  const aligns: (MdAlign | null)[] = [];
  for (let ci = 0; ci < nCols; ci++) aligns.push(ci < sep.length ? alignOf(sep[ci]) : null);

  const numCols = hitungNumCols(H, R, aligns, nCols);
  const pnCol = cariPnCol(H, R, numCols, nCols);
  return { table: { header: H, rows: R, aligns, numCols, pnCol, nCols }, next: j };
}

/** Kelas CSS kolom: dipakai <th> & <td>. '.num'/'.pn' SUDAH ada di `.tbl`. */
export function colClass(t: MdTable, ci: number): string {
  if (t.pnCol === ci) return "pn";
  if (t.numCols.has(ci)) return "num";
  if (t.aligns[ci] === "center") return "ctr";
  return "";
}

/** Judul kartu (mode ≥4 kolom di layar sempit): sel kolom PN, atau kolom 0. */
export function cardTitle(t: MdTable, row: string[], ri: number): string {
  const ci = t.pnCol ?? 0;
  const utama = (row[ci] ?? "").trim();
  if (utama) return utama;
  const lain = row.find((c) => (c || "").trim());
  return (lain || "").trim() || `Baris ${ri + 1}`;
}
