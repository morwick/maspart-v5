"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import MapPicker from "@/components/MapPicker";
import { ApiError, createOrder, getCartGudang, getCartWeight, getShippingRates, getPaymentMethods, type CartGudang, type ShippingRate, type GeoPlace } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { clearCart, getCart, hasPrice, hasWeight, removeFromCart, setQty, type CartItem } from "@/lib/cart";
import { ppnOf, totalOf } from "@/lib/order-ui";

const toNum = (s: string) => parseInt((s || "").replace(/[^\d]/g, ""), 10) || 0;
const rp = (n: number) => "Rp " + n.toLocaleString("id-ID");

export default function KeranjangPage() {
  const router = useRouter();
  const [items, setItems] = useState<CartItem[]>([]);
  const [note, setNote] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  // Alamat penerima
  const [rcpName, setRcpName] = useState("");
  const [rcpPhone, setRcpPhone] = useState("");
  const [rcpAddress, setRcpAddress] = useState("");
  const [rcpPostal, setRcpPostal] = useState("");
  const [mapOpen, setMapOpen] = useState(false);
  const [picked, setPicked] = useState<{ lat: number; lon: number } | null>(null);

  // Ongkir (berat dihitung otomatis dari jumlah item — bukan diisi pembeli)
  const [rates, setRates] = useState<ShippingRate[]>([]);
  const [rate, setRate] = useState<ShippingRate | null>(null);
  const [rateErr, setRateErr] = useState<string | null>(null);
  const [loadingRates, setLoadingRates] = useState(false);

  // Pembayaran (hanya online: VA/QRIS)
  const [gatewayOn, setGatewayOn] = useState(false);
  const channel = "snap";   // Midtrans Snap: semua metode dipilih di halaman bayar

  useEffect(() => {
    const token = getToken();
    if (!token) {
      router.replace("/login");
      return;
    }
    // Keranjang hanya untuk pembeli; admin/cabang diarahkan ke pencarian.
    if (getUser()?.role !== "pembeli") {
      router.replace("/search");
      return;
    }
    const c = getCart();
    setItems(c);
    getPaymentMethods(token)
      .then((m) => setGatewayOn(m.gateway_available))
      .catch(() => setGatewayOn(false));
  }, [router]);

  function refresh() {
    setItems(getCart());
  }

  // Keranjang tersimpan di browser LENGKAP dengan harganya saat part dimasukkan —
  // harga/stok bisa sudah berubah sejak itu. Keadaan dari server (`asal`) selalu
  // MENANG supaya yang dilihat = yang ditagih; harga lokal cuma dipakai selagi memuat.
  const [asal, setAsal] = useState<CartGudang | null>(null);
  const srv = (pn: string) => asal?.items.find((i) => i.part_number === pn);
  const gudangOf = (pn: string) => srv(pn)?.gudang || "";
  const hargaOf = (i: CartItem) => srv(i.part_number)?.harga ?? toNum(i.harga);
  const bisaBeli = (i: CartItem) => srv(i.part_number)?.bisa_dibeli ?? (hasPrice(i.harga) && hasWeight(i.berat));

  // SATU pesanan = SATU gudang (aturan pemilik): kurir tak bisa mengirim satu paket
  // dari dua kota, jadi part dari gudang berbeda harus jadi transaksi terpisah.
  // Keranjang boleh berisi banyak gudang, tapi checkout hanya untuk gudang TERPILIH;
  // sisanya tetap di keranjang untuk pesanan berikutnya.
  const gudangList = [...new Set(items.map((i) => gudangOf(i.part_number)).filter(Boolean))];
  const [gudangPilih, setGudangPilih] = useState("");
  const gudangAktif = gudangPilih && gudangList.includes(gudangPilih)
    ? gudangPilih
    : (asal?.utama || gudangList[0] || "");
  const lintasGudang = gudangList.length > 1;
  // Item yang IKUT checkout kali ini (item gudang lain ditahan, bukan dihapus).
  const itemsBeli = items.filter((i) => !gudangAktif || gudangOf(i.part_number) === gudangAktif);

  // Harga Accurate SUDAH termasuk PPN 12% → pajak bukan tambahan, hanya komponen.
  // Total = barang (harga Accurate) + ongkir (satu-satunya angka dari aplikasi).
  const subtotal = itemsBeli.reduce((n, i) => n + hargaOf(i) * i.qty, 0);
  const ppn = ppnOf(subtotal);
  const total = totalOf(subtotal, rate?.price || 0);
  // Part yang tak bisa dibeli (harga/berat/stok) → blokir checkout sampai dihapus.
  const blokir = items.filter((i) => !bisaBeli(i));
  const totalQty = itemsBeli.reduce((n, i) => n + i.qty, 0);
  const [weightGrams, setWeightGrams] = useState(0);
  const weightKg = weightGrams / 1000;
  // Tanda-tangan isi keranjang (PN:qty) → ambil ulang berat hanya saat isi berubah.
  const cartSig = items.map((i) => `${i.part_number}:${i.qty}`).join(",");
  // Ongkir & berat dihitung HANYA untuk gudang terpilih.
  const beliSig = itemsBeli.map((i) => `${i.part_number}:${i.qty}`).join(",");

  useEffect(() => {
    const token = getToken();
    if (!token || items.length === 0) {
      setWeightGrams(0);
      return;
    }
    let alive = true;
    // Gudang pengirim tiap item (= titik asal ongkir) — pembeli berhak tahu sebelum bayar.
    getCartGudang(token, items.map((i) => ({ part_number: i.part_number, qty: i.qty })))
      .then((r) => { if (alive) setAsal(r); })
      .catch(() => { if (alive) setAsal(null); });
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cartSig]);

  // Berat KIRIM hanya untuk item gudang terpilih (yang benar-benar akan dipesan).
  useEffect(() => {
    const token = getToken();
    if (!token || itemsBeli.length === 0) {
      setWeightGrams(0);
      return;
    }
    // Estimasi sementara dulu (1 kg/item) supaya UI langsung punya angka,
    // lalu pertajam dengan berat sesungguhnya dari backend.
    setWeightGrams(Math.max(1000, totalQty * 1000));
    let alive = true;
    getCartWeight(token, itemsBeli.map((i) => ({ part_number: i.part_number, qty: i.qty })))
      .then((r) => { if (alive) setWeightGrams(r.weight_grams); })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [beliSig]);

  // Berat berubah → ongkir lama tidak berlaku lagi, reset pilihan.
  useEffect(() => {
    setRates([]);
    setRate(null);
    setRateErr(null);
  }, [weightGrams]);

  async function cekOngkir() {
    const token = getToken();
    if (!token) return router.replace("/login");
    setLoadingRates(true);
    setRateErr(null);
    setRates([]);
    setRate(null);
    try {
      const r = await getShippingRates(token, weightGrams, subtotal, rcpPostal.trim(),
        itemsBeli.map((i) => ({ part_number: i.part_number, qty: i.qty })));
      if (r.error) setRateErr(r.error);
      setRates(r.rates);
    } catch (err) {
      setRateErr(err instanceof Error ? err.message : "Gagal cek ongkir");
    } finally {
      setLoadingRates(false);
    }
  }

  async function process() {
    const token = getToken();
    if (!token) return router.replace("/login");
    if (!itemsBeli.length) return;
    if (blokir.length) {
      setError(
        `Belum bisa dibeli: ${blokir
          .map((i) => `${i.part_number} (${srv(i.part_number)?.alasan || "data belum lengkap"})`)
          .join(", ")}. Hapus dari keranjang dulu.`,
      );
      return;
    }
    if (!rcpName.trim() || !rcpPhone.trim() || !rcpAddress.trim() || !rcpPostal.trim()) {
      setError("Lengkapi alamat penerima (nama, no. HP, alamat, kode pos) dulu.");
      return;
    }
    if (!gatewayOn) {
      setError("Pembayaran online (VA/QRIS) belum aktif. Hubungi admin.");
      return;
    }
    setBusy(true);
    setError(null);
    try {
      const res = await createOrder(token, {
        note,
        items: itemsBeli.map((i) => ({ part_number: i.part_number, qty: i.qty, name: i.name })),
        courier: rate?.courier,
        courier_service: rate?.service,
        shipping_cost: rate?.price || 0,
        weight_grams: weightGrams,
        payment_method: "gateway",
        payment_channel: channel,
        recipient_name: rcpName.trim() || undefined,
        recipient_phone: rcpPhone.trim() || undefined,
        recipient_address: rcpAddress.trim() || undefined,
        recipient_postal: rcpPostal.trim() || undefined,
      });
      // Hanya item yang JADI dipesan yang keluar dari keranjang — part dari gudang
      // lain tetap tersimpan untuk transaksi berikutnya.
      if (lintasGudang) itemsBeli.forEach((i) => removeFromCart(i.part_number));
      else clearCart();
      router.push(`/pesanan/${encodeURIComponent(res.order_code)}`);
    } catch (err) {
      if (err instanceof ApiError && err.status === 401) {
        clearSession();
        return router.replace("/login");
      }
      setError(err instanceof Error ? err.message : "Gagal membuat pesanan");
    } finally {
      setBusy(false);
    }
  }

  return (
    <AppShell active="/keranjang" title="Keranjang" sub="Tinjau part, pilih ekspedisi, lalu proses pembelian">
      <div className="mx-auto w-full max-w-4xl px-4 py-5 sm:px-6">
        {error && <div className="alert alert-error" style={{ marginBottom: 16 }}>{error}</div>}

        {items.length === 0 ? (
          <div className="surface grid place-items-center" style={{ height: 220, color: "var(--ink-500)", gap: 10 }}>
            <div>Keranjang kosong.</div>
            <Link href="/search" className="btn btn-primary btn-sm">Cari Part</Link>
          </div>
        ) : (
          <>
            {blokir.length > 0 && (
              <div className="alert alert-error" style={{ marginBottom: 16 }}>
                Part berikut <b>tidak bisa dibeli</b>:{" "}
                {blokir
                  .map((i) => `${i.part_number} — ${srv(i.part_number)?.alasan || "data belum lengkap"}`)
                  .join("; ")}. Hapus dari keranjang untuk melanjutkan.
              </div>
            )}

            {/* Keranjang lintas gudang: satu pesanan hanya bisa dari satu gudang
                (kurir tak bisa mengirim satu paket dari dua kota). Pembeli memesan
                bergantian — part gudang lain tetap tersimpan di keranjang. */}
            {lintasGudang && (
              <div className="surface surface-pad" style={{ marginBottom: 16 }}>
                <div style={{ fontSize: 13, marginBottom: 8 }}>
                  📦 Keranjang berisi part dari <b>{gudangList.length} gudang</b>. Satu pesanan hanya
                  bisa dari <b>satu gudang</b>, jadi part dari gudang lain <b>tetap tersimpan</b> dan
                  bisa dipesan setelah ini.
                </div>
                <div className="flex flex-wrap gap-2">
                  {gudangList.map((g) => {
                    const n = items.filter((i) => gudangOf(i.part_number) === g).length;
                    const aktif = g === gudangAktif;
                    return (
                      <button
                        key={g}
                        onClick={() => setGudangPilih(g)}
                        className="rounded-lg px-3 py-1.5"
                        style={{
                          fontSize: 12.5,
                          border: "1px solid " + (aktif ? "var(--brand-600)" : "var(--ink-200)"),
                          background: aktif ? "var(--brand-50)" : "var(--paper)",
                          fontWeight: aktif ? 600 : 500,
                        }}
                      >
                        Gudang {g} · {n} part{aktif ? " — dipesan sekarang" : ""}
                      </button>
                    );
                  })}
                </div>
              </div>
            )}

            <div className="surface" style={{ overflow: "hidden" }}>
              <table className="tbl">
                <thead>
                  <tr>
                    <th>Part Number</th>
                    <th>Nama</th>
                    <th className="num">Harga</th>
                    <th style={{ width: 110 }}>Qty</th>
                    <th className="num">Subtotal</th>
                    <th style={{ width: 44 }} />
                  </tr>
                </thead>
                <tbody>
                  {items.map((i) => (
                    <tr
                      key={i.part_number}
                      style={itemsBeli.includes(i) ? undefined : { opacity: 0.45 }}
                      title={itemsBeli.includes(i) ? undefined : "Gudang lain — dipesan di transaksi berikutnya"}
                    >
                      <td className="pn">{i.part_number}</td>
                      <td>
                        {i.name}
                        {!bisaBeli(i) && (
                          <span className="pill pill-warn" style={{ marginLeft: 8 }}>
                            {srv(i.part_number)?.alasan || "belum bisa dibeli"}
                          </span>
                        )}
                        {gudangOf(i.part_number) && (
                          <div style={{ fontSize: 11.5, color: "var(--ink-500)", marginTop: 2 }}>
                            🚚 Dikirim dari gudang <b>{gudangOf(i.part_number)}</b>
                          </div>
                        )}
                      </td>
                      <td className="num mono">
                        {hargaOf(i) > 0 ? rp(hargaOf(i)) : <span className="pill pill-warn">—</span>}
                      </td>
                      <td>
                        <input
                          type="number"
                          min={1}
                          value={i.qty}
                          onChange={(e) => {
                            setQty(i.part_number, Number(e.target.value) || 1);
                            refresh();
                          }}
                          className="input"
                          style={{ width: 80, height: 32 }}
                        />
                      </td>
                      <td className="num mono">{rp(hargaOf(i) * i.qty)}</td>
                      <td>
                        <button className="btn btn-danger btn-sm" title="Hapus" onClick={() => { removeFromCart(i.part_number); refresh(); }}>✕</button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>

            {/* Alamat Penerima */}
            <div className="surface surface-pad mt-4">
              <div className="mb-3 flex flex-wrap items-center gap-2">
                <div style={{ fontSize: 14, fontWeight: 600 }}>📍 Alamat Penerima</div>
                <span className="grow" />
                <button type="button" className="btn btn-secondary btn-sm" onClick={() => setMapOpen(true)}>🗺️ Pilih dari peta</button>
              </div>
              <div className="grid gap-3 sm:grid-cols-2">
                <div>
                  <label className="mb-1 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>Nama penerima</label>
                  <input className="input" value={rcpName} onChange={(e) => setRcpName(e.target.value)} placeholder="Nama lengkap" />
                </div>
                <div>
                  <label className="mb-1 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>No. HP</label>
                  <input className="input" value={rcpPhone} onChange={(e) => setRcpPhone(e.target.value)} placeholder="08xxxxxxxxxx" />
                </div>
                <div className="sm:col-span-2">
                  <label className="mb-1 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>Alamat lengkap</label>
                  <textarea className="textarea" rows={2} value={rcpAddress} onChange={(e) => setRcpAddress(e.target.value)} placeholder="Jalan, no, RT/RW, kelurahan, kecamatan, kota/kabupaten, provinsi" />
                </div>
                <div>
                  <label className="mb-1 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>Kode pos</label>
                  <input className="input" value={rcpPostal} onChange={(e) => setRcpPostal(e.target.value.replace(/[^\d]/g, ""))} placeholder="mis. 10110" style={{ maxWidth: 160 }} />
                  <div style={{ fontSize: 11, color: "var(--ink-400)", marginTop: 4 }}>Dipakai untuk hitung ongkir.</div>
                </div>
              </div>
            </div>

            {/* Ekspedisi & Ongkir */}
            <div className="surface surface-pad mt-4">
              <div className="mb-3 flex flex-wrap items-center gap-3">
                <div style={{ fontSize: 14, fontWeight: 600 }}>🚚 Ekspedisi & Ongkir</div>
                <div className="grow" />
                <span
                  style={{ fontSize: 12.5, color: "var(--ink-600)" }}
                  title="Berat yang ditagih kurir: yang lebih besar antara berat asli dan berat volumetrik (p×l×t ÷ 6000). Barang besar tapi ringan ditagih dari ukurannya."
                >
                  Berat kirim: <b>{weightKg} kg</b>
                </span>
                <button onClick={cekOngkir} disabled={loadingRates} className="btn btn-secondary btn-sm">
                  {loadingRates ? "Mengecek…" : "Cek Ongkir"}
                </button>
              </div>

              {rateErr && <div className="alert alert-error" style={{ marginBottom: 10 }}>{rateErr}</div>}

              {/* Asal kirim: pembeli tahu barangnya berangkat dari mana sebelum bayar. */}
              {gudangAktif && (
                <div style={{ fontSize: 12.5, color: "var(--ink-600)", marginBottom: 10 }}>
                  🚚 Ongkir dihitung dari <b>Gudang {gudangAktif}</b>
                  {lintasGudang ? ` — untuk ${itemsBeli.length} part dari gudang ini saja.` : "."}
                </div>
              )}

              {rates.length > 0 ? (
                <div className="grid gap-2 sm:grid-cols-2">
                  {rates.map((r, idx) => {
                    const active = rate?.courier === r.courier && rate?.service === r.service;
                    return (
                      <button
                        key={idx}
                        onClick={() => setRate(r)}
                        className="flex items-center justify-between rounded-lg px-3 py-2 text-left"
                        style={{
                          border: "1px solid " + (active ? "var(--brand-600)" : "var(--ink-200)"),
                          background: active ? "var(--brand-50)" : "var(--paper)",
                        }}
                      >
                        <div>
                          <div style={{ fontSize: 13, fontWeight: 600 }}>
                            {r.courier_name} · {r.service}
                          </div>
                          {r.etd && <div style={{ fontSize: 11.5, color: "var(--ink-500)" }}>estimasi {r.etd}</div>}
                        </div>
                        <span className="mono" style={{ fontWeight: 700, color: active ? "var(--brand-700)" : "var(--ink-800)" }}>{rp(r.price)}</span>
                      </button>
                    );
                  })}
                </div>
              ) : (
                !rateErr && <p style={{ fontSize: 12.5, color: "var(--ink-500)" }}>Atur berat lalu klik <b>Cek Ongkir</b> untuk memilih ekspedisi.</p>
              )}
            </div>

            {/* Pembayaran — online via Midtrans (Snap) */}
            <div className="surface surface-pad mt-4">
              <div className="mb-3" style={{ fontSize: 14, fontWeight: 600 }}>💳 Pembayaran Online</div>
              {gatewayOn ? (
                <div style={{ fontSize: 12.5, color: "var(--ink-600)", lineHeight: 1.5 }}>
                  Setelah pesanan dibuat, Anda diarahkan ke halaman pembayaran aman{" "}
                  <b>Midtrans</b> untuk memilih metode — <b>Virtual Account, QRIS, e-wallet,
                  atau kartu</b>. Pembayaran terverifikasi otomatis.
                </div>
              ) : (
                <div className="alert alert-error" style={{ marginBottom: 0 }}>
                  Pembayaran online belum aktif. Hubungi admin.
                </div>
              )}
            </div>

            <div className="mt-4 grid gap-4 md:grid-cols-[1fr_320px]">
              <div className="surface surface-pad">
                <label className="mb-1.5 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>Catatan / tujuan pesanan</label>
                <textarea className="textarea" rows={3} value={note} onChange={(e) => setNote(e.target.value)} placeholder="Mis. restok cabang, untuk unit HOWO-7 …" />
              </div>
              <div className="surface surface-pad flex flex-col gap-2">
                <div className="flex items-center justify-between" style={{ fontSize: 13 }}>
                  <span style={{ color: "var(--ink-500)" }}>Subtotal barang</span>
                  <span className="mono">{rp(subtotal)}</span>
                </div>
                <div className="flex items-center justify-between" style={{ fontSize: 12.5 }}>
                  <span style={{ color: "var(--ink-400)" }} title="Harga sudah termasuk PPN — sama dengan dokumen Accurate">
                    Termasuk PPN 12%
                  </span>
                  <span className="mono" style={{ color: "var(--ink-400)" }}>{rp(ppn)}</span>
                </div>
                <div className="flex items-center justify-between" style={{ fontSize: 13 }}>
                  <span style={{ color: "var(--ink-500)" }}>Ongkir{rate ? ` (${rate.courier_name})` : ""}</span>
                  <span className="mono">{rate ? rp(rate.price) : "—"}</span>
                </div>
                <div className="divider" />
                <div className="flex items-center justify-between">
                  <span style={{ fontWeight: 600 }}>Total</span>
                  <span className="mono" style={{ fontSize: 18, fontWeight: 700, color: "var(--brand-700)" }}>{rp(total)}</span>
                </div>
                <button onClick={process} disabled={busy || blokir.length > 0 || itemsBeli.length === 0} className="btn btn-primary btn-lg mt-1" style={{ width: "100%" }}>
                  {busy
                    ? "Memproses…"
                    : blokir.length > 0
                      ? "Ada part yang belum bisa dibeli"
                      : lintasGudang
                        ? `Proses Pembelian — Gudang ${gudangAktif} (${itemsBeli.length} part)`
                        : "Proses Pembelian"}
                </button>
                <p style={{ fontSize: 11.5, color: "var(--ink-400)" }}>
                  Harga part dihitung dari sistem saat pesanan dibuat. Ongkir opsional.
                </p>
              </div>
            </div>
          </>
        )}
      </div>

      <MapPicker
        open={mapOpen}
        initial={picked}
        onClose={() => setMapOpen(false)}
        onPick={(p: GeoPlace) => {
          setRcpAddress(p.display_name || p.address || "");
          if (p.postal) setRcpPostal(p.postal);
          setPicked({ lat: p.lat, lon: p.lon });
          setRates([]);
          setRate(null);
          setMapOpen(false);
        }}
      />
    </AppShell>
  );
}
