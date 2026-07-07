"""Endpoint /api/populasi/kolom — semua nilai satu kolom dari hasil filter
(tanpa paginasi) utk tombol 'Salin No. Rangka'. Service populasi di-mock.
"""
import pandas as pd
import pytest

from app.routers import populasi as router_mod
from app.services import populasi

DF = pd.DataFrame({
    "NO": [1, 2, 3],
    "CUSTOMER": ["PT A", "PT A", "PT B"],
    "NOMOR RANGKA": ["LZZ111", "LZZ222", None],
    "MODEL": ["HOWO", "HOWO", "NX400"],
})


@pytest.fixture
def fake_populasi(monkeypatch):
    monkeypatch.setattr(populasi, "columns", lambda: [str(c) for c in DF.columns])
    monkeypatch.setattr(
        populasi, "query",
        lambda q="", filters=None: DF[DF["CUSTOMER"] == filters["CUSTOMER"]].reset_index(drop=True)
        if filters and "CUSTOMER" in filters else DF,
    )
    monkeypatch.setattr(populasi, "sort_df", lambda df, sort, direction="asc": df)


def test_default_kolom_rangka_dan_buang_kosong(fake_populasi):
    res = router_mod.kolom_values(q="", filters="", sort="", dir="asc", kolom="", _user={})
    assert res["kolom"] == "NOMOR RANGKA"
    assert res["values"] == ["LZZ111", "LZZ222"]   # baris tanpa rangka dibuang
    assert res["jumlah"] == 2


def test_mengikuti_filter(fake_populasi):
    res = router_mod.kolom_values(
        q="", filters='{"CUSTOMER": "PT A"}', sort="", dir="asc", kolom="", _user={})
    assert res["values"] == ["LZZ111", "LZZ222"]


def test_kolom_eksplisit(fake_populasi):
    res = router_mod.kolom_values(q="", filters="", sort="", dir="asc", kolom="MODEL", _user={})
    assert res["kolom"] == "MODEL"
    assert res["values"] == ["HOWO", "HOWO", "NX400"]


def test_kolom_tak_dikenal_jatuh_ke_rangka(fake_populasi):
    res = router_mod.kolom_values(q="", filters="", sort="", dir="asc", kolom="NGACO", _user={})
    assert res["kolom"] == "NOMOR RANGKA"
