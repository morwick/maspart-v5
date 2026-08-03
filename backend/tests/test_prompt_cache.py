"""Prompt-cache DeepSeek: system prompt utama WAJIB identik byte-per-byte antar-user
satu peran — cache hanya kena pada prefix yang sama persis, dan dulu baris
'Username:' di puncak prompt membuat ~28rb token cache-miss untuk TIAP user.

Identitas user (username + gudang cabang) tidak boleh hilang: ia pindah ke pesan
system [PENGGUNA] kecil menjelang akhir percakapan (murah, di luar prefix besar).
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


def test_username_disuntik_sebagai_system_terpisah_sebelum_pesan_user_terakhir(monkeypatch):
    monkeypatch.setattr(A, "_branch_scope", lambda u: "01.Jakarta")
    msgs = _capture_messages(monkeypatch, CABANG)

    assert msgs[0]["role"] == "system"
    assert "cab-jambi-77" not in msgs[0]["content"]              # prompt utama bersih

    idx = [i for i, m in enumerate(msgs)
           if m["role"] == "system" and "[PENGGUNA] Username: cab-jambi-77." in (m["content"] or "")]
    assert idx, "baris identitas user hilang dari percakapan"
    assert "CABANG gudang: 01.Jakarta" in msgs[idx[0]]["content"]
    # Tepat sebelum pesan user terakhir → dekat pertanyaan, di luar prefix besar.
    assert idx[0] == len(msgs) - 2
    assert msgs[-1]["role"] == "user"


def test_prefix_pesan_identik_antar_user(monkeypatch):
    """Yang dilihat DeepSeek: pesan pertama (prompt raksasa) sama byte-per-byte
    untuk dua pembeli berbeda → prefix cache dibagi bersama."""
    monkeypatch.setattr(A, "_branch_scope", lambda u: None)
    a = _capture_messages(monkeypatch, PEMBELI_A)
    b = _capture_messages(monkeypatch, PEMBELI_B)

    assert a[0] == b[0]
    assert "[PENGGUNA] Username: budi." in a[-2]["content"]
    assert "[PENGGUNA] Username: sari." in b[-2]["content"]
