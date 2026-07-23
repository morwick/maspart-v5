"""
Konfigurasi backend — dibaca dari environment / file .env.

Setara dengan blok [supabase] di .streamlit/secrets.toml milik app Streamlit,
tapi DECOUPLED: backend tidak bergantung pada st.secrets sama sekali.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import ClassVar

from pydantic_settings import BaseSettings, SettingsConfigDict

# Root backend/ (file ini = backend/app/core/config.py → parents[2] = backend/)
_BACKEND_DIR = Path(__file__).resolve().parents[2]


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_BACKEND_DIR / ".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ── Supabase ──
    supabase_url: str = ""
    supabase_key: str = ""
    supabase_service_key: str = ""  # untuk akses Storage bucket private
    supabase_table: str = "users"
    supabase_data_bucket: str = "data"

    # ── Lingkungan ──
    app_env: str = "dev"  # "dev" | "prod"/"production" — mengaktifkan validasi keamanan ketat

    # ── JWT ──
    jwt_secret: str = "dev-secret-ganti-di-produksi"
    jwt_algorithm: str = "HS256"
    jwt_expire_minutes: int = 720  # 12 jam — sama dengan SESSION_TIMEOUT_MINUTES

    # ── Data ──
    data_dir: str = "../data"

    # ── Cari by Foto (galeri embedding lokal) ──
    # Path file CSV hasil export tabel `part_image_index` (kolom: id, part_number,
    # sims_url, embedding, indexed_by, indexed_at). Bila diisi/ditemukan, pencarian
    # foto dicocokkan LANGSUNG dari file ini (tidak query database lagi).
    # Relatif terhadap backend/ atau absolut. Kosong = cari di lokasi default.
    image_index_csv: str = ""

    # ── CORS ──
    cors_origins: str = "http://localhost:3000,http://127.0.0.1:3000"

    # ── Proxy tepercaya (untuk rate-limit / IP klien) ──
    # Jumlah reverse-proxy tepercaya di depan app (mis. Traefik = 1). Dipakai
    # untuk mengambil IP klien asli dari X-Forwarded-For dari sisi KANAN, supaya
    # header XFF yang dipalsukan klien tak bisa melewati rate-limiter.
    trusted_proxies: int = 1

    # ── Ongkir (RajaOngkir / Komerce) ──
    rajaongkir_api_key: str = ""             # key dari rajaongkir.komerce.id
    biteship_api_key: str = ""               # (opsional, tidak dipakai default)
    ship_origin_postal: str = "10110"        # kode pos gudang asal/pusat
    ship_origin_id: str = ""                 # opsional: ID lokasi RajaOngkir asal (kalau diisi, tdk perlu cari via kode pos)
    ship_default_item_grams: int = 1000      # estimasi berat per item (gram)

    # ── Notifikasi Telegram (bot @BotFather) — notif pesanan masuk/lunas ke admin ──
    telegram_bot_token: str = ""             # token bot dari @BotFather
    telegram_chat_id: str = ""               # chat/grup tujuan notif (bisa >1, pisah koma)

    # ── Pembayaran (Midtrans Snap) ──
    midtrans_server_key: str = ""            # Server Key dashboard Midtrans (Basic auth + signature webhook)
    midtrans_client_key: str = ""            # Client Key (opsional; untuk Snap.js embed di frontend)
    midtrans_sandbox: bool = True            # True = sandbox (uji), False = production
    public_base_url: str = "http://127.0.0.1:8001"  # URL publik situs (finish redirect /pesanan/<code> & webhook /api/payments/webhook)

    # ── (Deprecated) Payment API Komerce — digantikan Midtrans, dibiarkan agar env lama tak error ──
    payment_api_key: str = ""
    payment_sandbox: bool = True
    payment_callback_secret: str = ""
    payment_base_url: str = ""

    # ── Accurate Online (ERP — stok live) ──
    # Auto-login SSO ke account.accurate.id → iris.accurate.id (lihat services/accurate.py).
    # device_id & uid STABIL per akun/perusahaan (diambil sekali dari browser); tanpa
    # keduanya, hanya mode sesi-manual (file data/accurate_session.json) yang aktif.
    accurate_username: str = ""      # email login Accurate
    accurate_password: str = ""      # password Accurate (plaintext; dibungkus saat kirim)
    accurate_device_id: str = ""     # device-id browser (agar dianggap "device dikenal", tak minta OTP email)
    accurate_uid: str = ""           # uniqueId database/perusahaan (open.do?uid=)
    accurate_host: str = "iris.accurate.id"  # host aplikasi perusahaan (zona)
    accurate_auto_quotation: bool = True      # buat Penawaran Accurate otomatis saat order lunas

    @property
    def accurate_login_configured(self) -> bool:
        return bool(self.accurate_username and self.accurate_password
                    and self.accurate_device_id and self.accurate_uid)

    # ── Asisten AI (DeepSeek — OpenAI-compatible API) ──
    deepseek_api_key: str = ""                       # key dari platform.deepseek.com
    deepseek_base_url: str = "https://api.deepseek.com"  # base URL OpenAI-compatible
    deepseek_model: str = "deepseek-chat"            # "deepseek-chat" | "deepseek-reasoner"

    @property
    def ai_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    # ── Asisten AI — provider CADANGAN (OpenAI-compatible; dipakai otomatis saat
    #    DeepSeek 402 saldo habis / 429 / 5xx / jaringan). Kosong = fitur mati.
    #    Diisi via UI Coolify (AI_FALLBACK_*); vendor bebas asal OpenAI-compatible. ──
    ai_fallback_base_url: str = ""
    ai_fallback_api_key: str = ""
    ai_fallback_model: str = ""

    @property
    def ai_fallback_configured(self) -> bool:
        return bool(self.ai_fallback_base_url and self.ai_fallback_api_key
                    and self.ai_fallback_model)

    # ── Telematics/GPS armada (Sinotruk Fleet Service — www.sg.sinotruksfs.com) ──
    # Portal pelacakan GPS/IoT armada, TERPISAH dari SIMS & EPC (server, auth,
    # kredensial berbeda). Login RSA (services/telematics.py). Diisi di UI Coolify
    # (⛔ jangan .env langsung — ditimpa Coolify); password TIDAK di-commit.
    telematics_username: str = ""
    telematics_password: str = ""

    @property
    def telematics_configured(self) -> bool:
        return bool(self.telematics_username and self.telematics_password)

    @property
    def midtrans_app_base(self) -> str:
        """Base URL Snap (buat transaksi) — app.* host."""
        return "https://app.sandbox.midtrans.com" if self.midtrans_sandbox else "https://app.midtrans.com"

    @property
    def midtrans_api_base(self) -> str:
        """Base URL Core API (cek status transaksi) — api.* host."""
        return "https://api.sandbox.midtrans.com" if self.midtrans_sandbox else "https://api.midtrans.com"

    @property
    def payment_configured(self) -> bool:
        return bool(self.midtrans_server_key)

    @property
    def telegram_configured(self) -> bool:
        return bool(self.telegram_bot_token and self.telegram_chat_id)

    @property
    def data_path(self) -> Path:
        p = Path(self.data_dir)
        if not p.is_absolute():
            p = (_BACKEND_DIR / p).resolve()
        return p

    @property
    def image_index_csv_path(self) -> Path | None:
        """Lokasi file CSV galeri embedding (part_image_index).
        Urutan cari: setting eksplisit → root project → folder data/.
        Mengembalikan None bila tidak ada file yang ditemukan."""
        _PROJECT_ROOT = _BACKEND_DIR.parent
        candidates: list[Path] = []
        if self.image_index_csv:
            p = Path(self.image_index_csv)
            candidates.append(p if p.is_absolute() else (_BACKEND_DIR / p))
        candidates.append(_PROJECT_ROOT / "part_image_index_rows.csv")
        candidates.append(self.data_path / "part_image_index_rows.csv")
        for c in candidates:
            try:
                if c.is_file():
                    return c.resolve()
            except OSError:
                continue
        return None

    @property
    def cors_origin_list(self) -> list[str]:
        return [o.strip() for o in self.cors_origins.split(",") if o.strip()]

    @property
    def storage_key(self) -> str:
        """Key untuk Storage — service_key kalau ada, fallback ke anon key."""
        return self.supabase_service_key or self.supabase_key

    @property
    def supabase_configured(self) -> bool:
        return bool(
            self.supabase_url
            and self.supabase_key
            and "supabase.co" in self.supabase_url
            and not self.supabase_url.startswith("https://xxxxxxxxxxx")
        )

    _DEFAULT_JWT_SECRET: ClassVar[str] = "dev-secret-ganti-di-produksi"

    # Nilai APP_ENV yang secara EKSPLISIT berarti non-produksi. Apa pun di luar ini
    # (termasuk kosong / salah ketik / tak di-set) diperlakukan sebagai PRODUKSI →
    # gerbang keamanan fail-CLOSED (lupa set APP_ENV tak boleh melonggarkan validasi).
    _DEV_ENVS: ClassVar[frozenset] = frozenset(
        {"dev", "development", "local", "test", "testing", "ci"}
    )

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() not in self._DEV_ENVS

    def security_issues(self) -> list[str]:
        """Daftar masalah keamanan konfigurasi (kosong = aman)."""
        issues: list[str] = []
        if not self.jwt_secret or self.jwt_secret == self._DEFAULT_JWT_SECRET:
            issues.append(
                "JWT_SECRET masih default/kosong — token bisa dipalsukan. "
                "Set JWT_SECRET ke string acak yang panjang."
            )
        if len(self.jwt_secret or "") < 32 and self.jwt_secret != self._DEFAULT_JWT_SECRET:
            issues.append("JWT_SECRET terlalu pendek (<32 karakter); gunakan minimal 32 karakter acak.")
        # Midtrans: webhook diverifikasi via signature SHA512 memakai Server Key —
        # tak perlu secret callback terpisah. (Server Key sudah wajib agar aktif.)
        return issues

    def validate_security(self) -> list[str]:
        """Gagalkan startup pada konfigurasi tak aman.
        - JWT_SECRET default/kosong = FATAL di lingkungan APA PUN (jangan pernah
          boot dengan secret yang diketahui publik — tak bergantung APP_ENV yang
          mudah lupa di-set).
        - Masalah lain (secret pendek, callback secret kosong) fatal hanya di
          production; di dev dikembalikan sebagai peringatan."""
        if not self.jwt_secret or self.jwt_secret == self._DEFAULT_JWT_SECRET:
            raise RuntimeError(
                "JWT_SECRET belum di-set (masih default/kosong). Server menolak "
                "start: set env JWT_SECRET ke string acak minimal 32 karakter."
            )
        issues = self.security_issues()
        if issues and self.is_production:
            raise RuntimeError(
                "Konfigurasi keamanan tidak aman untuk production (APP_ENV=prod):\n  - "
                + "\n  - ".join(issues)
            )
        return issues


@lru_cache
def get_settings() -> Settings:
    return Settings()
