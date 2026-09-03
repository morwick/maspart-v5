"""Walk pohon unit EPC: peminta serentak berbagi SATU walk, bukan walk dobel.

Latar (giliran nyata 2026-09-03, VIN LZZ8CUWD9TB112414): cache walk baru terisi
SETELAH walk selesai (±30 dtk untuk 465 node / 118 panggilan pohon). Dalam
rentang itu ada DUA peminta yang lazim datang bersamaan untuk frame yang sama —
thread prefetch chat (`warm_items_index`) dan tool EPC di ronde pertama — dan
dulu keduanya menjalankan walk PENUH sendiri-sendiri: 2× 118 panggilan pohon,
2× `_atlas_root` (9,8 dtk sekali jalan), ~20 koneksi serentak ke server EPC yang
justru memperlambat keduanya.

Di sini yang diuji perilakunya, bukan waktunya: peminta kedua WAJIB menunggu dan
memakai hasil walk pertama.
"""
import threading

from app.services import ai_assistant as A       # facade ai_parts/ (exec 1 namespace)
from app.services import epc_bom as E

FRAME = "TB112414"

# Pohon minimal: root(1) → dua anak leaf. Satu walk = 1 panggilan _tree_node.
_POHON = {
    1: [{"id": 11, "partListId": 111, "code": "A-1", "name": "Node A", "leaf": True},
        {"id": 12, "partListId": 112, "code": "B-1", "name": "Node B", "leaf": True}],
}


def _pasang_pohon(monkeypatch, mulai: threading.Event | None = None):
    """Stub EPC + hitung panggilannya. `mulai` menahan panggilan pohon pertama
    supaya thread kedua dijamin tiba SELAGI walk pertama masih berjalan — tanpa
    itu tesnya balapan dan bisa lolos karena kebetulan cepat."""
    hitung = {"root": 0, "tree": 0}

    def _root(frame):
        hitung["root"] += 1
        return {"rootId": 1, "orderNo": "X"}

    def _tree(frame, root_id, part_id):
        hitung["tree"] += 1
        if mulai is not None:
            mulai.wait(5)
        return {"data": _POHON.get(part_id, [])}

    monkeypatch.setattr(E, "_atlas_root_cached", _root)
    monkeypatch.setattr(E, "_atlas_root", _root)
    monkeypatch.setattr(E, "_tree_node", _tree)
    E._asm_nodes_cache.clear()
    E._asm_build_locks.clear()
    return hitung


def test_dua_peminta_serentak_hanya_satu_walk(monkeypatch):
    mulai = threading.Event()
    hitung = _pasang_pohon(monkeypatch, mulai)
    hasil: list = [None, None]

    def _kerja(i):
        hasil[i] = E._walk_all_nodes(FRAME)

    t1 = threading.Thread(target=_kerja, args=(0,))
    t2 = threading.Thread(target=_kerja, args=(1,))
    t1.start()
    # Beri thread-1 kesempatan memegang lock walk sebelum thread-2 masuk.
    threading.Event().wait(0.05)
    t2.start()
    mulai.set()
    t1.join(10)
    t2.join(10)

    assert hitung["tree"] == 1, "walk kedua tak boleh menembak pohon EPC lagi"
    assert hitung["root"] == 1, "root EPC (9,8 dtk) tak boleh diambil dua kali"
    for h in hasil:
        assert h["found"] is True
        assert [n["id"] for n in h["nodes"]] == [11, 12]


def test_walk_kedua_setelah_selesai_pakai_cache(monkeypatch):
    hitung = _pasang_pohon(monkeypatch)

    a = E._walk_all_nodes(FRAME)
    b = E._walk_all_nodes(FRAME)

    assert a["found"] is True and b["found"] is True
    assert hitung["tree"] == 1 and hitung["root"] == 1


def test_walk_terpotong_tak_dicache_dan_boleh_diulang(monkeypatch):
    """Budget habis = walk BOLONG. Ia sengaja tak di-cache, dan lock per-frame
    tak boleh diam-diam mengubah itu jadi 'sekali gagal, gagal selamanya'."""
    hitung = _pasang_pohon(monkeypatch)
    monkeypatch.setattr(E, "_ASM_BUDGET", 0)

    a = E._walk_all_nodes(FRAME)
    b = E._walk_all_nodes(FRAME)

    assert a["incomplete"] is True and b["incomplete"] is True
    assert hitung["tree"] == 0        # budget 0 → node pertama pun tak dibuka
    assert hitung["root"] == 2        # tak di-cache → peminta kedua walk lagi


def test_tool_pengguna_indeks_item_dapat_batas_waktu_180(monkeypatch):
    """Build indeks item unit DINGIN terukur 165,6 dtk (2026-09-03). Dengan
    watchdog 90 dtk, panggilan pertama untuk unit baru selalu divonis timeout."""
    for nama in ("filter_unit", "cari_part_di_unit", "uraikan_assembly"):
        assert A._tool_timeout(nama) == A._TOOL_TIMEOUT_BERAT_S
        # Yang dinaikkan HANYA batas waktu — plafon panggilan tetap longgar,
        # karena plafon ketat di tool inilah yang dulu bikin negatif palsu.
        assert A._plafon_tool(nama) == A._MAX_CALLS_PER_TOOL
    assert A._tool_timeout("cek_massal_part") == A._TOOL_TIMEOUT_S
