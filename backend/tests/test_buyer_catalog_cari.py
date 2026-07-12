"""Pencarian etalase paham bahasa lapangan — dan TIDAK melebar ke seisi kategori.

Regresi yang dijaga: cari 'kampas rem' dulu mengembalikan 139 produk yang dipimpin
'24" brake chamber'. Dua sebabnya: (1) karena query memuat kata 'rem', payung
kategori menyeret SEMUA part rem (chamber, ABS, air compressor); (2) urutan 'Paling
relevan' tak melihat query sama sekali — hanya READY → berfoto → nama alfabetis.
"""
import pytest

from app.services import buyer_catalog as BC

NAMA = [
    "Brake friction plate",          # kampas — inilah yang dicari
    "Brake lining",                  # kampas
    "Brake shoe assembly",           # kampas
    '24" brake chamber (Left, L=40)',  # BUKAN kampas (sekategori saja)
    "ABS sensor (WABCO)",            # BUKAN kampas
    "Air compressor",                # BUKAN kampas
    "Oil filter",                    # beda kategori
]


@pytest.fixture
def produk():
    return [{"part_number": f"PN{i}", "name": n, "_hay": f"PN{i} {n.upper()}",
             "_flat": f"PN{i}", "kategori": ["rem"], "foto": None, "harga": 100_000,
             "harga_display": "Rp 100.000", "berat": 1000,
             "gudang": {"01.Jakarta": 3}, "stok_total": 3}
            for i, n in enumerate(NAMA)]


def _nama(hasil):
    return [p["name"] for p, _rank in sorted(hasil, key=lambda r: (r[1], r[0]["name"]))]


def test_kampas_rem_hanya_kampas_bukan_seisi_kategori(produk):
    nama = _nama(BC._match_q(produk, "kampas rem"))
    assert "Brake friction plate" in nama and "Brake lining" in nama
    assert "Brake shoe assembly" in nama
    assert not any("chamber" in n.lower() for n in nama)      # ⛔ payung kategori
    assert not any("abs" in n.lower() or "compressor" in n.lower() for n in nama)


def test_kata_kategori_polos_tetap_menyeret_sekeluarga(produk):
    """'rem' polos memang dimaksudkan menampilkan seluruh keluarga part rem —
    payung kategori tetap hidup untuk query satu kata."""
    nama = _nama(BC._match_q(produk, "rem"))
    assert any("chamber" in n.lower() for n in nama)
    assert nama[0].startswith("Brake friction plate")   # kampas tetap paling relevan


def test_nama_inggris_langsung_peringkat_teratas(produk):
    hasil = BC._match_q(produk, "brake lining")
    assert [p["name"] for p, _ in hasil] == ["Brake lining"]
    assert hasil[0][1] == 0          # cocok langsung = peringkat 0


def test_urutan_relevan_memakai_peringkat_query(monkeypatch, produk):
    """'Paling relevan' harus menaruh kampas di atas brake chamber, walaupun
    'brake chamber' lebih dulu secara alfabet dan sama-sama READY."""
    monkeypatch.setattr(BC, "_products", lambda: produk)
    monkeypatch.setattr(BC.gudang, "buyer_label", lambda k: "01.Jakarta")
    monkeypatch.setattr(BC.part_index, "gudang_names", lambda: ["01.Jakarta"])
    monkeypatch.setattr(BC.reservations, "reserved_map", lambda force=False: {})
    out = BC.catalog_page("roni", "jakarta", q="kampas rem", sort="relevan")
    nama = [i["name"] for i in out["items"]]
    assert nama and nama[0].lower().startswith("brake ")
    assert not any("chamber" in n.lower() for n in nama)
