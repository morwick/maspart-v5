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

    # ── Ongkir (RajaOngkir / Komerce) ──
    rajaongkir_api_key: str = ""             # key dari rajaongkir.komerce.id
    biteship_api_key: str = ""               # (opsional, tidak dipakai default)
    ship_origin_postal: str = "10110"        # kode pos gudang asal/pusat
    ship_origin_id: str = ""                 # opsional: ID lokasi RajaOngkir asal (kalau diisi, tdk perlu cari via kode pos)
    ship_default_item_grams: int = 1000      # estimasi berat per item (gram)

    # ── Pembayaran (Payment API Komerce) ──
    payment_api_key: str = ""                # key "Payment API" dari dashboard Komerce
    payment_sandbox: bool = True             # True = mode sandbox (uji), False = produksi
    payment_callback_secret: str = ""        # secret untuk verifikasi signature webhook
    payment_base_url: str = ""               # override base URL (kalau kosong, dipilih dari payment_sandbox)
    public_base_url: str = "http://127.0.0.1:8001"  # URL publik backend (untuk callback_url webhook)

    # ── Asisten AI (DeepSeek — OpenAI-compatible API) ──
    deepseek_api_key: str = ""                       # key dari platform.deepseek.com
    deepseek_base_url: str = "https://api.deepseek.com"  # base URL OpenAI-compatible
    deepseek_model: str = "deepseek-chat"            # "deepseek-chat" | "deepseek-reasoner"

    @property
    def ai_configured(self) -> bool:
        return bool(self.deepseek_api_key)

    @property
    def payment_api_base(self) -> str:
        if self.payment_base_url:
            return self.payment_base_url.rstrip("/")
        return (
            "https://api-sandbox.collaborator.komerce.id"
            if self.payment_sandbox
            else "https://api.collaborator.komerce.id"
        )

    @property
    def payment_configured(self) -> bool:
        return bool(self.payment_api_key)

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

    @property
    def is_production(self) -> bool:
        return self.app_env.strip().lower() in ("prod", "production", "live")

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
        if self.payment_configured and not self.payment_callback_secret:
            issues.append(
                "PAYMENT_CALLBACK_SECRET kosong — webhook pembayaran tidak bisa diverifikasi keasliannya."
            )
        return issues

    def validate_security(self) -> list[str]:
        """Di production: gagalkan startup jika ada masalah keamanan.
        Di dev: hanya kembalikan daftar peringatan (caller yang mencetak)."""
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
