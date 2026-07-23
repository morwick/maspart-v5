"""gambar_exploded MULTI-PN (P6 paket handal 2026-07-23): log produksi — model
memanggil tool 4× beruntun (1 PN/panggilan). `pn` kini array / string ';':
semua PN diproses SEKALI panggil, status per-PN, jalur 1 PN tak berubah."""
import pytest

from app.services import ai_assistant as ai

ADMIN = {"username": "admin", "role": "admin"}


@pytest.fixture
def impl(monkeypatch):
    """Stub kedua impl: atlas kenal PN berawalan 'AZ'; mesin kenal awalan '61'."""
    calls = {"atlas": [], "mesin": []}

    def atlas(args, user):
        pn = (args.get("pn") or "").strip()
        calls["atlas"].append(dict(args))
        if pn.startswith("AZ"):
            return {"found": True, "pn": pn,
                    "gambar": [{"image_id": f"img-{pn}-1", "nama_figure": f"FIG {pn}"},
                               {"image_id": f"img-{pn}-2", "nama_figure": "FIG b"},
                               {"image_id": f"img-{pn}-3", "nama_figure": "FIG c"}]}
        return {"found": False, "error": f"PN {pn} tak ketemu di Atlas."}

    def mesin(args, user):
        pn = (args.get("pn") or "").strip()
        calls["mesin"].append(dict(args))
        if pn.startswith("61"):
            return {"found": True, "pn": pn,
                    "gambar": [{"image_id": f"weichai-{pn}", "nama_figure": "ENGINE"}]}
        return {"found": False, "error": "tak ada di mesin"}

    monkeypatch.setattr(ai, "_gambar_exploded_atlas_impl", atlas)
    monkeypatch.setattr(ai, "_gambar_exploded_mesin_impl", mesin)
    return calls


def test_array_dua_pn_satu_panggilan(impl):
    r = ai._t_gambar_exploded(
        {"rangka": "RT108966", "pn": ["AZ111", "AZ222"], "kategori": "rem"}, ADMIN)
    assert r["found"] and [p["pn"] for p in r["per_pn"]] == ["AZ111", "AZ222"]
    assert len(impl["atlas"]) == 2
    # tiap gambar berlabel pn asal; maks 2 figure per PN
    assert [g["pn"] for g in r["gambar"]] == ["AZ111", "AZ111", "AZ222", "AZ222"]


def test_string_pemisah_dan_dedup(impl):
    r = ai._t_gambar_exploded({"rangka": "RT108966", "pn": "AZ111; AZ111, AZ222"}, ADMIN)
    assert len(impl["atlas"]) == 2                 # dedup: AZ111 sekali
    assert r["pns"] == ["AZ111", "AZ222"]


def test_cap_4_pn(impl):
    ai._t_gambar_exploded(
        {"rangka": "RT108966", "pn": ["AZ1", "AZ2", "AZ3", "AZ4", "AZ5", "AZ6"]}, ADMIN)
    assert len(impl["atlas"]) == 4


def test_pn_nihil_disebut_jujur(impl):
    r = ai._t_gambar_exploded({"rangka": "RT108966", "pn": ["AZ111", "XX999"]}, ADMIN)
    assert r["found"] is True and r["pn_nihil"] == ["XX999"]
    baris_nihil = next(p for p in r["per_pn"] if p["pn"] == "XX999")
    assert baris_nihil["found"] is False and baris_nihil.get("error")
    assert "jangan mengarang" in r["catatan"]


def test_fallback_mesin_per_pn(impl):
    """PN mesin Weichai di tengah daftar → auto-fallback per-PN tetap jalan."""
    r = ai._t_gambar_exploded({"rangka": "RT108966", "pn": ["AZ111", "61500"]}, ADMIN)
    baris = next(p for p in r["per_pn"] if p["pn"] == "61500")
    assert baris["found"] is True and baris["sumber_dipakai"] == "mesin_weichai"
    assert any(g["pn"] == "61500" for g in r["gambar"])


def test_satu_pn_jalur_lama_persis(impl):
    """Back-compat: 1 PN (string) = hasil impl apa adanya, tanpa per_pn/pns."""
    r = ai._t_gambar_exploded({"rangka": "RT108966", "pn": "AZ111"}, ADMIN)
    assert r["found"] and "per_pn" not in r and "pns" not in r
    assert len(r["gambar"]) == 3                   # tanpa pemangkasan 2-per-PN
    assert len(impl["atlas"]) == 1


def test_balon_diabaikan_di_mode_multi(impl):
    ai._t_gambar_exploded(
        {"rangka": "RT108966", "pn": ["AZ111", "AZ222"], "balon": 3}, ADMIN)
    assert all("balon" not in a for a in impl["atlas"])
    # mode 1 PN: balon tetap diteruskan
    ai._t_gambar_exploded({"rangka": "RT108966", "pn": "AZ111", "balon": 3}, ADMIN)
    assert impl["atlas"][-1].get("balon") == 3
