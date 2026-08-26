# Asisten menulis data ke Excel lampiran

> **LIVE 2026-08-26** — commit `18922f2`. Tool `sheet_tulis`.
> Aturan pemiliknya tetap: **file Excel dari user tidak pernah diubah formatnya** —
> asisten hanya MENGISI atau MEMBACA. Lihat `ai_export.isi_di_tempat`.

Ada **dua** alat penulis Excel di asisten, dan keduanya menulis ke **file user itu
sendiri** (format, rumus, baris kop, sheet lain tak disentuh; kolom baru ditambah di
kanan):

| Alat | Untuk apa | Sumber nilainya |
|---|---|---|
| `sheet_isi_kolom` | Data MASPART yang **diturunkan dari Part Number**: stok total/per-gudang, harga Accurate, harga SIMS, nama part, PN pengganti, cross-ref, berat, dimensi, rencana pemenuhan, foto part, gambar exploded | Indeks Accurate / katalog / SIMS / EPC |
| `sheet_tulis` | Apa pun yang **user tentukan sendiri**: catatan, tanda, angka revisi, dan **RUMUS Excel** | Ucapan user, atau hasil tool di giliran yang sama |

⛔ `sheet_tulis` **tidak boleh diisi nilai karangan model**. Kalau user belum menyebut
nilainya, asisten harus bertanya.

---

## Yang bisa diminta user

Lampirkan Excel di chat, lalu:

| Kalimat user | Yang terjadi |
|---|---|
| "isi kolom Keterangan 'kirim batch 2' untuk PN A, B, C" | hanya baris ber-PN itu; PN yang muncul berkali-kali (daftar per gudang) ikut semua |
| "tulis 5 di baris 12 kolom Qty" | baris **12 yang user lihat di Excel**, bukan baris data ke-12 |
| "kolom Supplier isi MAS untuk semua baris" | semua baris data — baris TOTAL/pemisah **dilewati** |
| "buat kolom Total = Qty × Harga" | rumus **hidup** `=D4*E4`, `=D5*E5` … dihitung Excel saat dibuka |
| "tandai KURANG untuk baris yang stoknya di bawah qty" | disaring **di server** atas seluruh baris |
| "ganti keterangan baris ini jadi …" | perlu izin eksplisit → `timpa=true` |

### Rumus (yang paling berguna)

Referensi kolom ditulis dalam kurung kurawal dan diterjemahkan per baris:

```
rumus = "={Qty}*{Harga}"                       →  =D4*E4  di baris 4
rumus = '=IF({Stok}>={Qty},"OK","KURANG")'     →  =IF(F4>=D4,"OK","KURANG")
{baris}                                        →  nomor baris itu sendiri
```

Kolom baru berumus ikut mewarisi **format angka** dari nama kolomnya
(`Total Harga` → `"Rp"#,##0`), jadi hasilnya terbaca sebagai Rupiah, bukan angka telanjang.

### Syarat baris (`bila`)

`bila = {kolom, operator, nilai}` — operator: `kosong`, `terisi`, `sama`, `tidak_sama`,
`memuat`, `lebih_besar`, `lebih_kecil`. Nilai pembanding boleh **merujuk kolom lain**:
`nilai: "{Qty}"`.

Penyaringan sengaja dihitung **di server**, bukan oleh model: model hanya melihat 5 baris
contoh dari `sheet_ringkasan`, jadi ia tak mungkin menyaring 400 baris dengan benar.

---

## Pagar yang menjaga file user

1. **Sel yang sudah berisi tidak ditimpa** (`timpa=false` default). Kalau semua sel sasaran
   ternyata sudah berisi, tool **gagal terang-terangan** dan menyarankan `timpa=true` —
   bukan memberi kartu unduh yang menipu.
2. **Rumus milik user tidak pernah dirusak**, sel merge dilewati, dan baris header wajib
   masih cocok dengan hasil baca (kalau tidak, isian dibatalkan — lebih baik gagal daripada
   menulis ke sel yang salah).
3. **Nama kolom dicocokkan SATU ARAH.** Kolom baru bernama "Status Stok" tidak akan menyasar
   kolom **"Stok" milik user**, "Qty Kirim" tidak menyasar "Qty". Hanya arah aman yang
   dipertahankan: header user yang MEMUAT ketikan user (`keterangan` → `Keterangan Pengiriman`).
4. **Rumus hanya lewat jalur eksplisit.** Nilai biasa yang kebetulan diawali `=` tetap
   ditulis sebagai TEKS (`'=1+1`) — pagar anti formula-injection tetap berlaku untuk data
   dari SIMS/EPC/model.
5. **"Semua baris" melewati baris tanpa Part Number** — itu baris TOTAL, pemisah, atau
   sub-judul milik user. Menulis "MAS" di baris TOTAL-nya adalah kerusakan, bukan pengisian.
6. **Angka yang dilaporkan = yang benar-benar mendarat.** `sel_ditulis` diselaraskan dengan
   hasil nyata mesin isi-di-tempat; sasaran yang ternyata rumus user dilaporkan **0 sel**.
7. PN yang tak ada di file, nomor baris yang tak ada, dan plafon yang tercapai **selalu
   dilaporkan** — tak pernah dibuang diam-diam.

## Yang TIDAK didukung (sengaja)

- **Mengosongkan sel.** Hasil kosong tak pernah menghapus isi sel user.
- **Menambah BARIS baru.** Menyisipkan baris menggeser rumus user dan openpyxl tak
  memperbaruinya → risikonya merusak dokumen.
- **Rumus pada unggahan CSV / link Google Sheets.** File itu tak punya nomor sel asli
  (dibangun ulang), jadi rumusnya akan menunjuk sel yang salah → ditolak dengan penjelasan.

## Batas

| Hal | Batas |
|---|---|
| Ukuran unggahan | 10 MB |
| Baris dibaca | 5.000 (sisanya tetap ada di file, tapi tak terisi — dilaporkan) |
| Kolom dibaca | 40 |
| Sel per panggilan | 5.000 |
| Item `nilai` per panggilan | 2.000 |
| Umur lampiran di server | 2 jam sejak akses terakhir |

## Kode & test

- `backend/app/services/ai_sheet.py` — `tulis()`, `_kolom_tulis()`, `_cocok_bila()`,
  `_nilai_sel()`, `_bila_num()`
- `backend/app/services/ai_export.py` — `Rumus`, `isi_di_tempat()`
- Spec tool: `ai_parts/p2_tool_specs.py` · handler: `p3_tools_stok.py` ·
  dispatch: `p7_router_dispatch.py` · blok [LAMPIRAN] & kartu unduh: `p9_chat_loop.py`
- Test: `backend/tests/test_ai_sheet_tulis.py` (40 test, termasuk jalur chat penuh
  sampai kartu unduh)

Terkait: [`isi-stok-harga-sims-excel.md`](isi-stok-harga-sims-excel.md) — cara mengisi
stok & harga SIMS untuk file **di luar chat** (>10 MB / >5.000 baris) lewat API produksi.
