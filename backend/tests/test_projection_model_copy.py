"""Proyeksi per-tool SALINAN MODEL (_project_for_model / _dump_tool(name)):
field yang model tak butuh (ringkasan_kategori duplikat, export_id, image_id)
dibuang dari pesan tool yang dikirim ke model; hasil UTUH tetap dipakai
side-state (_capture_meta → kartu unduh Excel / gambar inline).

INVARIAN: hanya buang key — tanpa rename, baris ber-PN utuh, urutan dict
terjaga (catatan tetap di EKOR utk _cap_tool_content), hasil GAGAL lolos utuh.
"""
import json

from app.services import ai_assistant as ai

USER = {"username": "admin", "role": "admin"}


def _hasil_banding_massal():
    return {
        "found": True,
        "mode": "semua_kategori",
        "seragam_semua": False,
        "kategori_beda": [{"kategori_kode": "05", "kategori": "Kabin",
                           "seragam": False, "jumlah_kelompok": 2,
                           "jumlah_part_beda": 3}],
        "kategori_seragam": [{"kategori_kode": "01", "kategori": "Mesin",
                              "seragam": True, "jumlah_kelompok": 1,
                              "jumlah_part_beda": 0}],
        "ringkasan_kategori": [{"kategori_kode": "01"}, {"kategori_kode": "05"}],
        "perbandingan": {"seragam": False, "kesimpulan": "ADA kategori BERBEDA."},
        "export_id": "exp-abc123", "filename": "banding.xlsx",
        "judul": "Banding SEMUA kategori - 2 unit", "jumlah_baris": 2,
        "catatan": "Verdict DIHITUNG SISTEM.",
    }


def test_banding_massal_buang_duplikat_dan_export_id():
    p = ai._project_for_model("banding_rangka_massal", _hasil_banding_massal())
    assert "ringkasan_kategori" not in p and "export_id" not in p
    # data verdict & rincian tetap; filename/judul (dirujuk catatan kartu) tetap.
    assert p["kategori_beda"] and p["kategori_seragam"] and p["perbandingan"]
    assert p["filename"] == "banding.xlsx"


def test_catatan_tetap_di_ekor_serialisasi():
    s = ai._dump_tool(_hasil_banding_massal(), "banding_rangka_massal")
    assert s.rstrip("}").endswith('"catatan":"Verdict DIHITUNG SISTEM."')
    assert "export_id" not in s and "ringkasan_kategori" not in s


def test_gambar_image_id_dibuang_pn_utuh():
    r = {"found": True, "pn": "WG1234567890",
         "gambar": [{"image_id": "img-1", "filename": "fig.png",
                     "pn": "WG1234567890", "balon": "12",
                     "nama_figure": "CLUTCH", "kategori": "03"}],
         "catatan": "gambar terlampir"}
    for tool in ("gambar_exploded", "uraikan_assembly", "part_aus_dari_rangka"):
        s = ai._dump_tool(r, tool)
        assert "image_id" not in s and "img-1" not in s, tool
        assert "WG1234567890" in s and '"balon":"12"' in s, tool


def test_tool_lain_passthrough_byte_identik():
    r = {"found": True, "export_id": "exp-1", "parts": [{"pn": "A1"}]}
    assert ai._dump_tool(r, "cari_part") == ai._dump_tool(r)
    assert "exp-1" in ai._dump_tool(r, "cari_part")   # tak diproyeksi


def test_hasil_gagal_lolos_utuh():
    for r in ({"found": False, "error": "tidak ditemukan", "export_id": "e1"},
              {"denied": True, "error": "khusus admin"},
              {"error": "gangguan internal"}):
        p = ai._project_for_model("banding_rangka_massal", r)
        assert p == r                                  # steering error utuh
        assert ai._tool_fail_kind(p) == ai._tool_fail_kind(r)


def test_chat_loop_side_state_tetap_terisi(monkeypatch):
    """Integrasi: export_id/image_id hilang dari pesan tool utk model, tapi
    kartu unduh (excel_exports) & gambar inline (exploded_images) tetap ada."""
    monkeypatch.setattr(ai, "_system_prompt", lambda user: "system uji")
    monkeypatch.setattr(ai, "_tool_specs", lambda user, sheet_id="": [])
    monkeypatch.setattr(ai, "_allowed_tool_names",
                        lambda user, sheet_id="": {"banding_rangka_massal",
                                                   "gambar_exploded"})
    monkeypatch.setattr(ai, "_unit_name_tokens", lambda: set())
    monkeypatch.setattr(ai, "_prefetch_epc_rangka", lambda history: None)

    hasil = {
        "banding_rangka_massal": _hasil_banding_massal(),
        "gambar_exploded": {"found": True,
                            "gambar": [{"image_id": "img-9", "filename": "f.png",
                                        "pn": "WG1234567890", "balon": "3",
                                        "nama_figure": "GBX", "kategori": "04"}]},
    }
    monkeypatch.setattr(ai, "_run_tool",
                        lambda name, args, user, sheet_id="": hasil[name])
    seq = [
        {"choices": [{"message": {"content": "", "tool_calls": [
            {"id": "c1", "function": {"name": "banding_rangka_massal",
                                      "arguments": '{"rangka_list":["X","Y"]}'}},
            {"id": "c2", "function": {"name": "gambar_exploded",
                                      "arguments": '{"query":"gearbox"}'}},
        ]}, "finish_reason": "tool_calls"}]},
        {"choices": [{"message": {"content": "Perbandingan selesai."},
                      "finish_reason": "stop"}]},
    ]
    calls = {"n": 0}
    tool_msgs = []

    def fake(messages, tools, max_tokens=6000):
        tool_msgs.extend(m.get("content") or "" for m in messages
                         if m.get("role") == "tool")
        c = seq[min(calls["n"], len(seq) - 1)]
        calls["n"] += 1
        return c

    monkeypatch.setattr(ai, "_post_chat", fake)
    out = ai.chat(USER, [{"role": "user", "content": "banding unit X vs Y"}])
    # Salinan model bersih dari id opaque…
    gabung = "\n".join(tool_msgs)
    assert "exp-abc123" not in gabung and "img-9" not in gabung
    # …tapi side-state utk frontend tetap terisi dari hasil UTUH.
    assert any(e.get("id") == "exp-abc123" for e in out.get("excel_exports") or [])
    assert any(g.get("id") == "img-9" for g in out.get("exploded_images") or [])
