"use client";

import { useEffect, useState } from "react";
import { useRouter } from "next/navigation";
import Link from "next/link";
import AppShell from "@/components/AppShell";
import MapPicker from "@/components/MapPicker";
import { ApiError, createOrder, getCartWeight, getShippingRates, getPaymentMethods, type ShippingRate, type PaymentChannel, type GeoPlace } from "@/lib/api";
import { clearSession, getToken, getUser } from "@/lib/auth";
import { clearCart, getCart, hasPrice, hasWeight, removeFromCart, setQty, type CartItem } from "@/lib/cart";

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
  const [channels, setChannels] = useState<PaymentChannel[]>([]);
  const [channel, setChannel] = useState("qris");

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
      .then((m) => {
        setGatewayOn(m.gateway_available);
        setChannels(m.channels);
      })
      .catch(() => setGatewayOn(false));
  }, [router]);

  function refresh() {
    setItems(getCart());
  }

  const subtotal = items.reduce((n, i) => n + toNum(i.harga) * i.qty, 0);
  const ppn = Math.round(subtotal * 0.11);
  const total = subtotal + ppn + (rate?.price || 0);
  // Part tanpa harga / tanpa berat tidak bisa dibeli → blokir checkout sampai dihapus.
  const noPriceItems = items.filter((i) => !hasPrice(i.harga));
  const noWeightItems = items.filter((i) => !hasWeight(i.berat));
  // Berat dihitung otomatis oleh backend dari data berat part (fallback estimasi/item).
  const totalQty = items.reduce((n, i) => n + i.qty, 0);
  const [weightGrams, setWeightGrams] = useState(0);
  const weightKg = weightGrams / 1000;
  // Tanda-tangan isi keranjang (PN:qty) → ambil ulang berat hanya saat isi berubah.
  const cartSig = items.map((i) => `${i.part_number}:${i.qty}`).join(",");

  useEffect(() => {
    const token = getToken();
    if (!token || items.length === 0) {
      setWeightGrams(0);
      return;
    }
    // Estimasi sementara dulu (1 kg/item) supaya UI langsung punya angka,
    // lalu pertajam dengan berat sesungguhnya dari backend.
    setWeightGrams(Math.max(1000, totalQty * 1000));
    let alive = true;
    getCartWeight(token, items.map((i) => ({ part_number: i.part_number, qty: i.qty })))
      .then((r) => { if (alive) setWeightGrams(r.weight_grams); })
      .catch(() => {});
    return () => { alive = false; };
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [cartSig]);

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
      const r = await getShippingRates(token, weightGrams, subtotal, rcpPostal.trim());
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
    if (!items.length) return;
    if (noPriceItems.length) {
      setError(
        `Part tanpa harga belum bisa dibeli: ${noPriceItems.map((i) => i.part_number).join(", ")}. Hapus dari keranjang dulu.`,
      );
      return;
    }
    if (noWeightItems.length) {
      setError(
        `Part tanpa berat belum bisa dibeli: ${noWeightItems.map((i) => i.part_number).join(", ")}. Hapus dari keranjang dulu.`,
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
        items: items.map((i) => ({ part_number: i.part_number, qty: i.qty, name: i.name })),
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
      clearCart();
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
            {noPriceItems.length > 0 && (
              <div className="alert alert-error" style={{ marginBottom: 16 }}>
                Part berikut belum punya harga dan <b>tidak bisa dibeli</b>:{" "}
                {noPriceItems.map((i) => i.part_number).join(", ")}. Hapus dari keranjang untuk melanjutkan.
              </div>
            )}
            {noWeightItems.length > 0 && (
              <div className="alert alert-error" style={{ marginBottom: 16 }}>
                Part berikut belum punya data berat dan <b>tidak bisa dibeli</b>:{" "}
                {noWeightItems.map((i) => i.part_number).join(", ")}. Hapus dari keranjang untuk melanjutkan.
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
                    <tr key={i.part_number}>
                      <td className="pn">{i.part_number}</td>
                      <td>
                        {i.name}
                        {!hasWeight(i.berat) && (
                          <span className="pill pill-warn" style={{ marginLeft: 8 }} title="Berat belum ditetapkan admin">Tanpa berat</span>
                        )}
                      </td>
                      <td className="num mono">
                        {hasPrice(i.harga) ? (
                          i.harga
                        ) : (
                          <span className="pill pill-warn" title="Harga belum tersedia">Tanpa harga</span>
                        )}
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
                      <td className="num mono">{rp(toNum(i.harga) * i.qty)}</td>
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
                <span style={{ fontSize: 12.5, color: "var(--ink-600)" }}>
                  Berat: <b>{weightKg} kg</b>
                </span>
                <button onClick={cekOngkir} disabled={loadingRates} className="btn btn-secondary btn-sm">
                  {loadingRates ? "Mengecek…" : "Cek Ongkir"}
                </button>
              </div>

              {rateErr && <div className="alert alert-error" style={{ marginBottom: 10 }}>{rateErr}</div>}

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

            {/* Pembayaran — online (VA / QRIS) */}
            <div className="surface surface-pad mt-4">
              <div className="mb-3" style={{ fontSize: 14, fontWeight: 600 }}>💳 Pembayaran Online (VA / QRIS)</div>
              {gatewayOn ? (
                <div>
                  <label className="mb-1.5 block" style={{ fontSize: 12.5, fontWeight: 550, color: "var(--ink-700)" }}>Pilih channel</label>
                  <select className="select" value={channel} onChange={(e) => setChannel(e.target.value)} style={{ maxWidth: 280 }}>
                    {channels.map((c) => (
                      <option key={c.code} value={c.code}>{c.label}</option>
                    ))}
                  </select>
                  <div style={{ fontSize: 11.5, color: "var(--ink-500)", marginTop: 8 }}>
                    Bayar via Virtual Account atau QRIS — terverifikasi otomatis.
                  </div>
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
                  <span style={{ color: "var(--ink-500)" }}>Subtotal</span>
                  <span className="mono">{rp(subtotal)}</span>
                </div>
                <div className="flex items-center justify-between" style={{ fontSize: 13 }}>
                  <span style={{ color: "var(--ink-500)" }}>PPN (11%)</span>
                  <span className="mono">{rp(ppn)}</span>
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
                <button onClick={process} disabled={busy || noPriceItems.length > 0 || noWeightItems.length > 0} className="btn btn-primary btn-lg mt-1" style={{ width: "100%" }}>
                  {busy
                    ? "Memproses…"
                    : noPriceItems.length > 0
                      ? "Ada part tanpa harga"
                      : noWeightItems.length > 0
                        ? "Ada part tanpa berat"
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
