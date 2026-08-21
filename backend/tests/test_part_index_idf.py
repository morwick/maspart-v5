"""part_index.idf — bobot kelangkaan kata nama part (sumber skor relevansi).

Menguji fungsi ASLI (tanpa mock), dengan _state diisi langsung — dua jebakan
nyata yang pernah lolos justru karena idf di-mock di tes lain:
  • kata ≤2 huruf tidak pernah masuk part_name_index (lihat _process_file), jadi
    df=0. Kalau df=0 dihitung apa adanya, 'AS' jadi kata TERLANGKA.
  • kunci part_name_index masih membawa tanda baca ('SEAL,'), sehingga DF untuk
    'SEAL' bocor kalau tidak dipecah ulang.
"""
import math

import pytest

from app.services import part_index


@pytest.fixture
def state_bersih(monkeypatch):
    """Kembalikan _state ke kondisi semula setelah tes menyentuhnya."""
    asli = {k: part_index._state.get(k) for k in ("name_df", "name_rows")}
    yield
    part_index._state.update(asli)


def _set_df(df: dict, rows: int):
    part_index._state.update({"name_df": df, "name_rows": rows})


def test_kata_umum_lebih_rendah_dari_kata_langka(state_bersih):
    _set_df({"BOLT": 4000, "TURBOCHARGER": 12}, 5000)
    assert part_index.idf("turbocharger") > part_index.idf("bolt")


def test_tidak_peduli_huruf_besar_kecil(state_bersih):
    _set_df({"BOLT": 4000}, 5000)
    assert part_index.idf("bolt") == part_index.idf("BOLT") == part_index.idf("BoLt")


def test_kata_di_hampir_semua_baris_tak_pernah_negatif(state_bersih):
    """df > N bisa terjadi karena DF dijumlah lintas file katalog."""
    _set_df({"ASSY": 9000}, 5000)
    assert part_index.idf("assy") == 0.0


def test_kata_pendek_tak_dikenal_dapat_nilai_NETRAL_bukan_tertinggi(state_bersih):
    """'AS' dibuang indeks (≤2 huruf) → df=0. Ia TIDAK boleh jadi terlangka."""
    _set_df({"BOLT": 4000, "TURBOCHARGER": 12}, 5000)
    nilai_as = part_index.idf("as")
    assert nilai_as == part_index.IDF_NETRAL
    assert nilai_as < part_index.idf("turbocharger"), "kata pendek mendominasi skor"


def test_kata_pendek_dengan_df_KECIL_TAPI_BUKAN_NOL_tetap_netral(state_bersih):
    """⛔⛔ Kasus NYATA dari katalog produksi, bukan hipotetis.

    _process_file mengindeks token >2 KARAKTER dari txt.split(), jadi 'AS'
    sendirian tak masuk — tapi 'AS,' (berkoma) masuk, lalu pemecah DF menarik
    'AS' darinya. Terukur df=72 dari 6.066.949 baris → idf 11,33, TERTINGGI
    dari seluruh 5.613 kosakata, mengalahkan CRANKSHAFT (8,63).

    Memeriksa df==0 TIDAK menangkap ini. Panjang katanya yang menentukan."""
    _set_df({"AS": 72, "CRANKSHAFT": 1083, "BOLT": 127381}, 6_066_949)
    assert part_index.idf("as") == part_index.IDF_NETRAL
    assert part_index.idf("as") < part_index.idf("crankshaft"), \
        "'as' kembali mendominasi skor — df kecil menyesatkan lolos lagi"
    assert part_index.idf("as") < part_index.idf("bolt"), \
        "'as' bahkan mengalahkan kata paling umum"


def test_kata_3_huruf_tetap_dihitung_normal(state_bersih):
    """Batasnya HARUS di ≤2, bukan lebih longgar: 'OIL' (3 huruf) diindeks
    dengan benar, jadi df-nya tepercaya dan tak boleh ikut dinetralkan."""
    _set_df({"OIL": 41130, "CRANKSHAFT": 1083}, 6_066_949)
    assert part_index.idf("oil") != part_index.IDF_NETRAL
    assert part_index.idf("oil") < part_index.idf("crankshaft")


def test_salah_ketik_tak_diistimewakan(state_bersih):
    _set_df({"TURBOCHARGER": 12}, 5000)
    assert part_index.idf("turbochrgr") == part_index.IDF_NETRAL


def test_katalog_belum_terindeks_netral(state_bersih):
    _set_df({}, 0)
    assert part_index.idf("apapun") == part_index.IDF_NETRAL


def test_nilai_mengikuti_rumus_idf(state_bersih):
    _set_df({"SEAL": 999}, 5000)
    assert part_index.idf("seal") == pytest.approx(math.log(5001 / 1000))


# ── DF dibangun dari kunci indeks yang masih bertanda baca ───────────
def test_kunci_bertanda_baca_dipecah_saat_membangun_df():
    """'OIL SEAL, CRANKSHAFT' menghasilkan kunci 'SEAL,' — DF untuk 'SEAL'
    harus tetap menghitungnya, kalau tidak kata umum tampak langka."""
    nm_idx = {"OIL": [0, 1], "SEAL,": [0], "SEAL": [1], "CRANKSHAFT": [0]}
    df = {}
    for w, rows in nm_idx.items():
        for t in part_index._KATA_IDF_RE.findall(w):
            df[t] = df.get(t, 0) + len(rows)
    assert df["SEAL"] == 2, "tanda baca membuat DF terpecah"
    assert df["OIL"] == 2
