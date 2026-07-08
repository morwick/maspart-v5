"""Skema request/response (Pydantic)."""
from __future__ import annotations

from pydantic import BaseModel


class LoginRequest(BaseModel):
    username: str
    password: str


class UserOut(BaseModel):
    username: str
    role: str
    gudang: str | None = None  # key lokasi gudang terpilih (akun pembeli)


class TokenResponse(BaseModel):
    access_token: str
    token_type: str = "bearer"
    expires_in: int  # detik
    user: UserOut


class PartResult(BaseModel):
    file: str
    path: str
    sheet: str
    part_number: str
    part_name: str
    quantity: str
    stok: str
    harga: str
    berat: int = 0  # berat per item (gram); 0 = belum ditetapkan admin
    gudang: dict[str, int] = {}
    excel_row: int
    source: str = ""  # "" = database lokal, "sims" = nama diambil dari SIMS


class SaranPart(BaseModel):
    part_number: str = ""
    part_name: str = ""


class SearchResponse(BaseModel):
    term: str
    count: int           # total hasil (semua halaman)
    page: int
    page_size: int
    total_pages: int
    results: list[PartResult]  # hanya potongan halaman ini
    saran: list[SaranPart] = []  # "mungkin maksud Anda" — hanya diisi saat 0 hasil


class PartPhotos(BaseModel):
    part_number: str
    photos: list[str] = []
    source: str = ""  # "sims" | "part_photos" | ""


class ImageMatch(BaseModel):
    part_number: str
    part_name: str = ""
    sims_url: str
    similarity: float
    raw_similarity: float
    n_matches: int
    n_strong: int
    boost: float
    distance: float
    stok: str = "—"      # stok total (indeks Accurate / Excel) — tanpa breakdown gudang
    harga: str = "—"     # harga jual lokal
    tersedia: bool = False


class ImageSearchResponse(BaseModel):
    count: int
    results: list[ImageMatch]
    galeri_total: int = 0     # jumlah foto terindeks di galeri
    galeri_parts: int = 0     # jumlah part unik di galeri
    pesan: str | None = None  # penjelasan saat 0 hasil (cakupan galeri + tips)


class ImageLearnResponse(BaseModel):
    ok: bool
    pn: str
    duplikat: bool = False
    galeri_total: int = 0
    error: str | None = None


class CompareBest(BaseModel):
    shape_score: float
    color_score: float
    name_score: float | None
    overall: float
    verdict: str
    color: str
    i: int
    j: int


class CompareResponse(BaseModel):
    pn1: str
    pn2: str
    name1: str
    name2: str
    urls1: list[str]
    urls2: list[str]
    best: CompareBest | None
    error: str | None


class IndexStatus(BaseModel):
    indexed: bool
    indexed_at: str | None
    file_count: int
    sheet_count: int
    part_count: int
    stok_entries: int
    harga_entries: int
    gudang_names: list[str]
    data_dir: str
