"""Prompt-cache DeepSeek: system prompt utama WAJIB identik byte-per-byte antar-user
satu peran — cache hanya kena pada prefix yang sama persis, dan dulu baris
'Username:' di puncak prompt membuat ~28rb token cache-miss untuk TIAP user.

Identitas user (username + gudang cabang) tidak boleh hilang: ia pindah ke baris
[PENGGUNA] di dalam [CATATAN SISTEM] yang DIGABUNG ke pesan user terakhir.

⛔ BUKAN pesan `role:system` kedua: template DeepSeek mengangkat SEMUA pesan
system ke puncak prompt (sebelum spec tool), jadi pesan system yang berubah tiap
giliran mematikan cache spec tool + riwayat — terukur di produksi (Agu 2026):
panggilan pertama tiap giliran hanya kena cache ±20 rb dari ±47 rb token.
Kontrak: tepat SATU pesan system per permintaan.
"""
from app.services import ai_assistant as A

ADMIN_A = {"username": "mas", "role": "admin"}
ADMIN_B = {"username": "adminlain", "role": "admin"}
# NB: username tes dibuat UNIK ('jkt' polos ada di prompt sbg contoh singkatan).
CABANG = {"username": "cab-jambi-77", "role": "user"}
PEMBELI_A = {"username": "budi", "role": "pembeli"}
PEMBELI_B = {"username": "sari", "role": "pembeli"}


# ── System prompt utama: identik antar-user, bebas identitas ─────────────────
def test_prompt_identik_antar_user_satu_peran():
    assert A._system_prompt(PEMBELI_A) == A._system_prompt(PEMBELI_B)
    assert A._system_prompt({**CABANG, "username": "plb"}) == A._system_prompt(CABANG)


def test_prompt_tanpa_username_maupun_gudang_cabang(monkeypatch):
    monkeypatch.setattr(A, "_branch_scope", lambda u: "01.Jakarta")
    sp = A._system_prompt(CABANG)
    assert "cab-jambi-77" not in sp
    # NB: nama gudang (mis. '01.Jakarta') BOLEH ada — daftar gudang resmi statis &
    # sama utk semua user. Yang tak boleh: kalimat cabang PER-USER & baris Username.
    assert "Akun ini adalah CABANG gudang" not in sp
    assert "Username: " not in sp


def test_prompt_beda_peran_boleh_beda():
    # Kandungan per-peran memang beda (aturan admin ≠ pembeli) — itu sah;
    # cache-nya per peran, bukan per user.
    assert A._system_prompt(ADMIN_A) != A._system_prompt(PEMBELI_A)


def test_admin_see_all_kontennya_sama_dengan_admin_lain():
    # 'mas' (SEE_ALL) & admin biasa: keduanya admin → prompt identik.
    assert A._system_prompt(ADMIN_A) == A._system_prompt(ADMIN_B)


# ── Identitas user tetap sampai ke model, di tempat yang benar ───────────────
def _capture_messages(monkeypatch, user):
    """Jalankan chat() dengan _post_chat palsu; kembalikan messages yang terkirim."""
    sent: dict = {}

    def _fake_post(messages, tools, max_tokens=6000):
        sent["messages"] = [dict(m) for m in messages]
        return {"choices": [{"message": {"content": "Baik, ada yang bisa dibantu?"},
                             "finish_reason": "stop"}]}

    monkeypatch.setattr(A, "_post_chat", _fake_post)
    monkeypatch.setattr(A, "_prefetch_epc_rangka", lambda h: None)
    monkeypatch.setattr(A.ai_chat_log, "log_turn_async", lambda **kw: True)
    A.chat(user, [{"role": "user", "content": "halo"}])
    return sent["messages"]


def test_username_di_catatan_sistem_pesan_user_terakhir(monkeypatch):
    monkeypatch.setattr(A, "_branch_scope", lambda u: "01.Jakarta")
    msgs = _capture_messages(monkeypatch, CABANG)

    assert msgs[0]["role"] == "system"
    assert "cab-jambi-77" not in msgs[0]["content"]              # prompt utama bersih
    # Tepat SATU pesan system: pesan system kedua diangkat DeepSeek ke puncak
    # prompt & mematikan cache spec tool (lihat docstring modul).
    assert [m["role"] for m in msgs].count("system") == 1

    akhir = msgs[-1]
    assert akhir["role"] == "user"
    assert akhir["content"].startswith(A._CTX_BUKA)
    assert "[PENGGUNA] Username: cab-jambi-77." in akhir["content"]
    assert "CABANG gudang: 01.Jakarta" in akhir["content"]
    # Pertanyaan user asli tetap utuh di UJUNG, sesudah penutup catatan.
    assert akhir["content"].endswith(A._CTX_TUTUP + "\n\nhalo")


def test_prefix_pesan_identik_antar_user(monkeypatch):
    """Yang dilihat DeepSeek: seluruh prefix (semua pesan kecuali pesan user
    terakhir) sama byte-per-byte untuk dua pembeli berbeda → prefix cache
    dibagi bersama; yang berbeda hanya EKOR."""
    monkeypatch.setattr(A, "_branch_scope", lambda u: None)
    a = _capture_messages(monkeypatch, PEMBELI_A)
    b = _capture_messages(monkeypatch, PEMBELI_B)

    assert a[:-1] == b[:-1]
    assert "[PENGGUNA] Username: budi." in a[-1]["content"]
    assert "[PENGGUNA] Username: sari." in b[-1]["content"]


def test_pesan_terakhir_bukan_user_pun_tak_jadi_system(monkeypatch):
    """Riwayat klien yang berakhir di asisten: konteks jadi pesan USER
    tersendiri — tak pernah pesan system kedua."""
    monkeypatch.setattr(A, "_branch_scope", lambda u: None)
    msgs = [{"role": "user", "content": "halo"},
            {"role": "assistant", "content": "Ada yang bisa dibantu?"}]
    A._sisip_konteks(msgs, "[PENGGUNA] Username: budi.")
    assert [m["role"] for m in msgs] == ["user", "assistant", "user"]
    assert msgs[-1]["content"].startswith(A._CTX_BUKA)
    assert msgs[-1]["content"].endswith(A._CTX_TUTUP)
