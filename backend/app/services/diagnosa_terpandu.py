"""
DIAGNOSA TERPANDU — wawancara singkat keluhan lapangan → daftar penyebab
berperingkat, lengkap dengan langkah cek, bukti klaim garansi, dan kode
kesalahan terkait.

Masalah yang dipecahkan: keluhan sopir/mekanik hampir selalu KABUR ('mesin
ngempos di tanjakan', 'kolong bunyi'). Selama ini asisten menjawab satu tembak
(generik) atau mengarang pertanyaan klarifikasi yang berbeda-beda tiap giliran.
Di sini urutannya TETAP dan deterministik:
  1. keluhan → SISTEM (mesin, angin/rem, transmisi, suspensi, gardan,
     kelistrikan, kemudi, kabin/AC, SCR) lewat pemicu kata + kamus gejala;
  2. 1–3 pertanyaan pilihan yang KHAS sistem itu (kartu `tanya_user`) —
     pertanyaan yang sudah terjawab dari teks keluhan TIDAK ditanyakan lagi;
  3. jawaban → bobot ke daun POHON GEJALA (kurasi, di file ini) → peringkat;
  4. tiap hipotesis diisi BUKTI dari data nyata: profil klaim garansi
     (`warranty_profil`: berapa kali diganti, km median, mode rusak, part yang
     diganti bersamaan), kode kesalahan kanonik (`dtc_codes` per SPN), dan
     rujukan manual (`manual_teks`).

Pohon gejala sengaja KURASI, bukan dilatih dari klaim: teks keluhan klaim 68%
unik dan 40% di antaranya hanya menyebut nama part ("Flange Assembly is
wear") — tak cukup untuk menurunkan pertanyaan. Klaim dipakai sebagai BUKTI
frekuensi, bukan sumber struktur.

Kontrak stateless: kartu dijawab klien sebagai teks biasa ("<pertanyaan>
<opsi>\\n…"); giliran berikutnya model memanggil ulang dengan `keluhan` yang
sama + `jawaban` = teks itu. Parser mencocokkan OPSI (bukan pertanyaan), jadi
tetap bekerja bila model hanya meneruskan jawabannya. Pagar anti-tanya-ulang
per (user, keluhan) ada di `_SUDAH_TANYA`.
"""
from __future__ import annotations

import math
import re
import threading
import time

from . import dtc_codes, manual_teks, warranty_kasus, warranty_profil

# ── Pagar anti-tanya-ulang ───────────────────────────────────────────────────
# Kartu untuk (user, keluhan) yang sama hanya ditampilkan SEKALI per 15 menit.
# Panggilan berikutnya tanpa jawaban → lanjut dengan asumsi (bukan bertanya
# lagi) supaya model yang lupa meneruskan jawaban tak membuat ping-pong.
_SUDAH_TANYA: dict[str, float] = {}
_TANYA_TTL = 15 * 60.0
_TANYA_MAKS = 500
_lock = threading.Lock()

_BATAS_HIPOTESIS = 5
_BATAS_PART = 3          # komponen klaim per hipotesis
_BATAS_KODE = 2          # kode kesalahan per SPN
_HIPOTESIS_BERKODE = 3   # hanya N teratas yang dicarikan kode & manual
_BOBOT_PEMICU = 3.0
_BOBOT_BUKTI = 0.6       # × ln(1 + jumlah klaim) — penentu urutan saat seri
_MIN_HIPOTESIS = 3       # selalu ada pembanding untuk pertanyaan tindak lanjut
# manual_teks memberi skor 5–15 untuk kecocokan satu kata (halaman kode
# kesalahan acak); rujukan yang benar-benar membahas topik ≥ 20 (terukur).
_MANUAL_SKOR_MIN = 20.0

_LEWATI_RE = re.compile(r"\(lewati\)|\blewati\b|\bskip\b", re.I)


def _rx(*kata: str) -> list[re.Pattern]:
    return [re.compile(r"(?<!\w)" + k + r"(?!\w)", re.I) for k in kata]


# ── POHON GEJALA ─────────────────────────────────────────────────────────────
# Tiap sistem: pemicu (klasifikasi), tanya (kartu), sebab (daun).
# Daun: pemicu (regex pada keluhan), jawab {(id_tanya, opsi): bobot}, cek
# (langkah pemeriksaan urut), part (kata kunci Inggris → profil klaim),
# spn (kode kesalahan kanonik), manual (topik cari_manual).
# Opsi ditulis PERSIS sama di 'tanya' dan 'jawab' — parser mencocokkan teksnya.
# 'otomatis' = regex pada keluhan yang sudah menjawab opsi itu → tak ditanya.
_SISTEM: dict[str, dict] = {
    "mesin": {
        "label": "Mesin & bahan bakar",
        "pemicu": _rx("mesin", "engine", "ngempos", "tenaga", "power", "asap", "ngebul",
                      "overheat", "panas", "suhu", "oli", "solar", "brebet", "pincang",
                      "rpm", "turbo", "injektor", "injector", "starter", "susah hidup",
                      "tidak hidup", "mati mendadak", "boros", "derate", "limp",
                      "blow.?by", "kompresi", "radiator", "coolant", "air radiator"),
        "tanya": [
            {"id": "lampu", "teks": "Ada lampu peringatan yang menyala di panel?",
             "opsi": ["Lampu cek mesin (MIL)", "Lampu oli / suhu", "Tidak ada lampu"],
             "otomatis": {"Lampu cek mesin (MIL)": _rx("mil", "lampu cek mesin", "check engine", "lampu mesin"),
                          "Lampu oli / suhu": _rx("lampu oli", "lampu suhu", "lampu temperatur")}},
            {"id": "asap", "teks": "Asap knalpotnya bagaimana?",
             "opsi": ["Asap hitam", "Asap putih", "Asap biru", "Asap normal"],
             "otomatis": {"Asap hitam": _rx("asap hitam", "ngebul hitam", "hitam"),
                          "Asap putih": _rx("asap putih", "putih"),
                          "Asap biru": _rx("asap biru", "biru")}},
            {"id": "kapan", "teks": "Kapan gejalanya paling terasa?",
             "opsi": ["Saat mesin dingin", "Saat beban / tanjakan", "Terus-menerus"],
             "otomatis": {"Saat mesin dingin": _rx("dingin", "pagi"),
                          "Saat beban / tanjakan": _rx("tanjakan", "beban", "muatan", "nanjak", "menanjak")}},
        ],
        "sebab": [
            {"id": "solar", "nama": "Pasokan solar tersendat (saringan solar buntu, air di solar, masuk angin)",
             "pemicu": _rx("ngempos", "tenaga kurang", "kurang tenaga", "lemah", "tersendat", "brebet",
                           "mati.?mati", "mati mendadak", "solar", "masuk angin"),
             "jawab": {("asap", "Asap normal"): 1, ("kapan", "Saat beban / tanjakan"): 2,
                       ("lampu", "Tidak ada lampu"): 1},
             "cek": ["Ganti saringan solar kasar & halus, buang air di water separator",
                     "Cek kebocoran udara di jalur hisap (selang, seal saringan) lalu bleeding",
                     "Cek tekanan feed pump / tekanan bahan bakar"],
             "part": ["fuel filter", "water separator", "primary fuel filter", "feed pump", "fuel pump"],
             "spn": [94], "manual": "tekanan bahan bakar rendah"},
            {"id": "udara_turbo", "nama": "Pasokan udara kurang (saringan udara buntu, turbo lemah, selang intercooler bocor)",
             "pemicu": _rx("asap hitam", "ngebul", "boros", "turbo", "intercooler", "saringan udara",
                           "filter udara", "boost", "ngelak"),
             "jawab": {("asap", "Asap hitam"): 3, ("kapan", "Saat beban / tanjakan"): 1},
             "cek": ["Cek indikator/kondisi saringan udara",
                     "Cek selang & klem intercooler (retak, lepas, oli menumpuk)",
                     "Cek main poros turbo dan oli di sisi kompresor; ukur tekanan boost"],
             "part": ["air filter", "turbocharger", "intercooler", "intake hose", "air duct"],
             "spn": [102, 105], "manual": "tekanan boost turbocharger"},
            {"id": "injektor", "nama": "Injektor / tekanan common rail tidak normal",
             "pemicu": _rx("brebet", "pincang", "kasar", "rpm tidak stabil", "bergetar", "goyang",
                           "injektor", "injector", "rail", "nozzle"),
             "jawab": {("lampu", "Lampu cek mesin (MIL)"): 2, ("asap", "Asap hitam"): 1,
                       ("asap", "Asap putih"): 1, ("kapan", "Terus-menerus"): 1},
             "cek": ["Baca kode kesalahan ECU (cari_kode_kesalahan) — cari SPN rail/injektor",
                     "Tes balance injektor per silinder; cek koreksi injektor di diagnostik",
                     "Cek tekanan rail vs target, pompa tekanan tinggi, kualitas solar"],
             "part": ["fuel injector", "injector", "high pressure pump", "common rail", "injection pump"],
             "spn": [157, 651], "manual": "tekanan rail injektor"},
            {"id": "susah_hidup", "nama": "Mesin berputar tapi susah / tidak mau hidup",
             "pemicu": _rx("susah hidup", "tidak mau hidup", "sulit start", "susah start", "tidak hidup",
                           "gak hidup", "nggak hidup", "tak hidup", "distarter", "lama hidup"),
             "jawab": {("kapan", "Saat mesin dingin"): 2, ("asap", "Asap putih"): 1},
             "cek": ["Cek solar sampai ke rail (bleeding), saringan solar, kebocoran udara",
                     "Cek busi pijar / pemanas udara masuk (saat dingin)",
                     "Cek sinyal sensor crankshaft & camshaft; cek kompresi bila semua normal"],
             "part": ["glow plug", "feed pump", "crankshaft sensor", "camshaft sensor", "fuel filter"],
             "spn": [636, 723], "manual": "mesin sulit start"},
            {"id": "overheat", "nama": "Mesin overheat / suhu air tinggi",
             "pemicu": _rx("overheat", "panas", "suhu tinggi", "temperatur", "mendidih", "air radiator",
                           "coolant", "radiator", "kipas", "thermostat", "termostat", "water pump"),
             "jawab": {("lampu", "Lampu oli / suhu"): 3, ("kapan", "Saat beban / tanjakan"): 1,
                       ("asap", "Asap putih"): 1},
             "cek": ["Cek level & kebocoran coolant, tutup radiator, kisi radiator kotor",
                     "Cek thermostat (buka penuh) dan kerja kipas / visco fan",
                     "Cek water pump (rembes, bearing) dan selang radiator; cek gas buang di coolant"],
             "part": ["thermostat", "radiator", "water pump", "fan clutch", "fan", "radiator hose"],
             "spn": [110, 111], "manual": "suhu air pendingin tinggi"},
            {"id": "oli", "nama": "Tekanan oli rendah / oli berkurang / bocor",
             "pemicu": _rx("tekanan oli", "oli berkurang", "oli habis", "lampu oli", "oli bocor",
                           "rembes oli", "oli netes", "oli menetes", "nambah oli"),
             "jawab": {("lampu", "Lampu oli / suhu"): 2, ("asap", "Asap biru"): 2},
             "cek": ["Cek level & kualitas oli; cari rembesan (seal depan/belakang, karter, turbo, oil cooler)",
                     "Cek sensor tekanan oli vs pengukur manual",
                     "Cek pompa oli & saringan oli bila tekanan memang rendah"],
             "part": ["oil pressure sensor", "oil pump", "oil seal", "oil pan gasket", "oil cooler", "oil filter"],
             "spn": [100], "manual": "tekanan oli mesin rendah"},
            {"id": "blowby", "nama": "Kompresi lemah / blow-by tinggi (ring piston, liner, paking kepala silinder)",
             "pemicu": _rx("blow.?by", "asap dari breather", "asap dari tutup oli", "kompresi", "air masuk oli",
                           "oli bercampur", "oli seperti susu", "paking", "gasket"),
             "jawab": {("asap", "Asap putih"): 2, ("asap", "Asap biru"): 2, ("kapan", "Terus-menerus"): 1},
             "cek": ["Ukur tekanan blow-by di breather; cek oli di intake",
                     "Tes kompresi / leak-down per silinder",
                     "Cek coolant di oli atau gelembung di radiator (paking kepala silinder)"],
             "part": ["piston ring", "cylinder head gasket", "cylinder liner", "piston", "connecting rod bush"],
             "spn": [], "manual": "tekanan blow-by"},
            {"id": "elektrik_mesin", "nama": "Sensor / kabel mesin (RPM terkunci, derate, mati mendadak)",
             "pemicu": _rx("rpm terkunci", "derate", "limp", "tenaga dibatasi", "mati mendadak", "sensor",
                           "ecu", "kabel"),
             "jawab": {("lampu", "Lampu cek mesin (MIL)"): 3},
             "cek": ["Baca & catat semua kode kesalahan aktif (cari_kode_kesalahan)",
                     "Cek soket sensor (pedal gas, crank, cam, boost, suhu) dan ground",
                     "Cek tegangan aki saat mesin hidup (pengisian)"],
             "part": ["sensor", "wiring harness", "electronic accelerator", "connector"],
             "spn": [91, 84, 190], "manual": "kode kesalahan ECU"},
        ],
    },
    "angin_rem": {
        "label": "Sistem angin & rem",
        "pemicu": _rx("rem", "brake", "angin", "kompresor", "abs", "blong", "pakem", "tromol",
                      "kampas", "brake chamber", "dryer", "spring brake", "parkir"),
        "tanya": [
            {"id": "angin", "teks": "Bagaimana tekanan angin di panel?",
             "opsi": ["Angin lama isi / tidak penuh", "Angin cepat habis saat mesin mati", "Angin normal"],
             "otomatis": {"Angin lama isi / tidak penuh": _rx("lama isi", "tidak isi", "gak isi", "angin kurang", "tidak penuh"),
                          "Angin cepat habis saat mesin mati": _rx("cepat habis", "bocor angin", "angin habis", "desis")}},
            {"id": "rem", "teks": "Apa yang terjadi saat mengerem?",
             "opsi": ["Rem kurang pakem / blong", "Rem mengunci / tidak lepas", "Rem bunyi / bergetar", "Lampu ABS nyala"],
             "otomatis": {"Rem kurang pakem / blong": _rx("kurang pakem", "blong", "tidak pakem", "rem dalam", "rem jauh"),
                          "Rem mengunci / tidak lepas": _rx("mengunci", "terkunci", "tidak lepas", "nyangkut", "ngunci"),
                          "Rem bunyi / bergetar": _rx("bunyi", "berdecit", "bergetar", "kedut"),
                          "Lampu ABS nyala": _rx("abs")}},
        ],
        "sebab": [
            {"id": "kompresor", "nama": "Kompresor angin lemah / unloader-governor tidak bekerja",
             "pemicu": _rx("angin lama", "lama isi", "tidak isi", "kompresor", "angin kurang", "tekanan angin", "unloader", "governor"),
             "jawab": {("angin", "Angin lama isi / tidak penuh"): 3},
             "cek": ["Ukur waktu isi 0→8 bar; cek saringan udara kompresor",
                     "Cek unloader / governor (cut-in/cut-out) dan pipa dari kompresor (karbon)",
                     "Cek ring kompresor (oli keluar ke tangki angin)"],
             "part": ["air compressor", "unloader valve", "pressure regulator", "compressor"],
             "spn": [], "manual": "tekanan angin kompresor"},
            {"id": "bocor_angin", "nama": "Kebocoran angin (selang, katup 4 sirkuit, brake chamber, air dryer)",
             "pemicu": _rx("bocor angin", "desis", "angin habis", "bunyi angin", "cepat habis", "dryer"),
             "jawab": {("angin", "Angin cepat habis saat mesin mati"): 3},
             "cek": ["Semprot air sabun di sambungan, brake chamber, katup 4 sirkuit, relay valve",
                     "Cek air dryer (purge terus-menerus = katup dryer)",
                     "Cek tangki angin (buang air) dan katup drain"],
             "part": ["air dryer", "brake chamber", "four circuit protection valve", "air hose", "relay valve", "drain valve"],
             "spn": [], "manual": "kebocoran angin"},
            {"id": "kampas", "nama": "Kampas / tromol aus atau setelan rem (slack adjuster) longgar",
             "pemicu": _rx("kurang pakem", "blong", "rem dalam", "rem jauh", "kampas", "tromol", "tidak pakem", "aus"),
             "jawab": {("rem", "Rem kurang pakem / blong"): 3, ("angin", "Angin normal"): 1},
             "cek": ["Cek ketebalan kampas & kondisi tromol tiap roda",
                     "Cek stroke brake chamber dan setelan slack adjuster (otomatis/manual)",
                     "Cek tekanan angin saat mengerem (harus > 6 bar)"],
             "part": ["brake shoe", "brake lining", "brake drum", "slack adjuster", "brake chamber"],
             "spn": [], "manual": "penyetelan rem"},
            {"id": "mengunci", "nama": "Rem mengunci / spring brake tidak lepas (angin < 5 bar, relay valve, camshaft rem macet)",
             "pemicu": _rx("rem mengunci", "rem terkunci", "tidak lepas", "roda panas", "ban panas", "nyangkut", "ngunci", "rem tangan"),
             "jawab": {("rem", "Rem mengunci / tidak lepas"): 3, ("angin", "Angin lama isi / tidak penuh"): 1},
             "cek": ["Pastikan tekanan angin > 5,5 bar (spring brake lepas); cek katup rem parkir",
                     "Cek relay valve & quick release valve (angin tertahan)",
                     "Cek camshaft rem / bushing macet dan return spring kampas"],
             "part": ["spring brake chamber", "relay valve", "brake camshaft", "return spring", "hand brake valve"],
             "spn": [], "manual": "rem parkir spring brake"},
            {"id": "abs", "nama": "ABS: sensor roda / ring / modulator",
             "pemicu": _rx("abs", "lampu abs", "sensor roda", "modulator"),
             "jawab": {("rem", "Lampu ABS nyala"): 3},
             "cek": ["Baca blink code ABS (cari_kode_kesalahan unit ABS)",
                     "Cek celah & kotoran sensor roda vs ring, kabel sensor putus",
                     "Cek modulator / solenoid dan tegangan ECU ABS"],
             "part": ["abs sensor", "wheel speed sensor", "abs modulator", "abs ecu"],
             "spn": [789, 790, 791, 792], "manual": "abs sensor roda"},
            {"id": "rem_bunyi", "nama": "Rem bunyi / bergetar (tromol oval, kampas kaca, bearing roda, baut roda)",
             "pemicu": _rx("rem bunyi", "berdecit", "bergetar", "kedut", "gemeretak", "menggerung"),
             "jawab": {("rem", "Rem bunyi / bergetar"): 3},
             "cek": ["Cek keovalan tromol & kampas mengkilap/retak",
                     "Cek bearing roda dan kekencangan baut roda",
                     "Cek backing plate & pegas kampas"],
             "part": ["brake drum", "brake shoe", "wheel bearing", "wheel bolt", "backing plate"],
             "spn": [], "manual": "rem bergetar"},
        ],
    },
    "transmisi_kopling": {
        "label": "Transmisi & kopling",
        "pemicu": _rx("transmisi", "gearbox", "persneling", "gigi", "kopling", "clutch", "sinkron",
                      "synchro", "range", "selip", "pedal kopling", "oper gigi", "pindah gigi"),
        "tanya": [
            {"id": "kopling", "teks": "Bagaimana kerja koplingnya?",
             "opsi": ["Kopling selip (rpm naik, laju tidak)", "Pedal keras / tidak bebas", "Kopling bergetar / bunyi", "Kopling normal"],
             "otomatis": {"Kopling selip (rpm naik, laju tidak)": _rx("selip", "slip", "bau gosong"),
                          "Pedal keras / tidak bebas": _rx("pedal keras", "tidak bebas", "pedal ngempos", "pedal dalam"),
                          "Kopling bergetar / bunyi": _rx("kopling bergetar", "kopling bunyi", "getar")}},
            {"id": "gigi", "teks": "Ada masalah pada perpindahan gigi?",
             "opsi": ["Susah masuk gigi", "Gigi loncat / lepas sendiri", "Bunyi saat pindah gigi", "Tidak ada"],
             "otomatis": {"Susah masuk gigi": _rx("susah masuk", "sulit masuk", "keras masuk", "tidak bisa masuk", "gak bisa masuk"),
                          "Gigi loncat / lepas sendiri": _rx("loncat", "lepas sendiri", "netral sendiri"),
                          "Bunyi saat pindah gigi": _rx("bunyi saat", "krek", "gemeretak")}},
        ],
        "sebab": [
            {"id": "kampas_kopling", "nama": "Kampas kopling aus / selip (kampas, matahari, release bearing)",
             "pemicu": _rx("selip", "slip", "bau gosong", "kampas kopling", "rpm naik"),
             "jawab": {("kopling", "Kopling selip (rpm naik, laju tidak)"): 3, ("kopling", "Kopling bergetar / bunyi"): 1},
             "cek": ["Cek free play pedal & langkah booster",
                     "Cek ketebalan kampas dan permukaan matahari; cari oli di kampas (seal belakang mesin / seal input shaft)",
                     "Cek release bearing (bunyi saat pedal diinjak)"],
             "part": ["clutch disc", "clutch plate", "pressure plate", "release bearing", "separate bearing", "clutch"],
             "spn": [], "manual": "kopling selip"},
            {"id": "hidrolik_kopling", "nama": "Master / booster kopling atau udara di sistem hidrolik",
             "pemicu": _rx("pedal keras", "pedal ngempos", "tidak bebas", "master kopling", "booster", "minyak kopling", "pedal dalam"),
             "jawab": {("kopling", "Pedal keras / tidak bebas"): 3, ("gigi", "Susah masuk gigi"): 2},
             "cek": ["Cek level minyak kopling & kebocoran di master / booster",
                     "Bleeding sistem; cek angin masuk ke booster (Φ102)",
                     "Cek garpu & pen garpu kopling aus"],
             "part": ["clutch master cylinder", "clutch booster", "clutch slave cylinder", "clutch fork", "release bearing"],
             "spn": [], "manual": "booster kopling"},
            {"id": "sinkronizer", "nama": "Sinkronizer / ring sinkron aus",
             "pemicu": _rx("bunyi saat masuk", "gigi bunyi", "kasar masuk", "susah masuk", "krek", "sinkron", "synchro"),
             "jawab": {("gigi", "Bunyi saat pindah gigi"): 3, ("gigi", "Susah masuk gigi"): 2, ("kopling", "Kopling normal"): 1},
             "cek": ["Cek level & kualitas oli gearbox (gigi mana yang bermasalah?)",
                     "Cek kopling benar-benar bebas (kalau tidak, sinkronizer dipaksa)",
                     "Bongkar: ring sinkron, sliding sleeve, gigi kerucut"],
             "part": ["synchronizer", "synchronizer ring", "sliding sleeve", "shift fork", "synchronous"],
             "spn": [], "manual": "sinkronizer transmisi"},
            {"id": "gigi_loncat", "nama": "Gigi loncat / lepas sendiri (fork, detent, bearing poros)",
             "pemicu": _rx("loncat", "lepas sendiri", "gigi lepas", "netral sendiri", "pindah sendiri"),
             "jawab": {("gigi", "Gigi loncat / lepas sendiri"): 3},
             "cek": ["Cek garpu persneling & pen detent / pegas pengunci",
                     "Cek bearing main shaft / counter shaft (kelonggaran aksial)",
                     "Cek dudukan transmisi & tuas persneling"],
             "part": ["shift fork", "main shaft", "counter shaft", "tapered roller bearing", "fork shaft", "gear"],
             "spn": [], "manual": "gigi transmisi loncat"},
            {"id": "range", "nama": "Range / splitter (pressure switch, solenoid, angin ke transmisi)",
             "pemicu": _rx("range", "high low", "gigi tinggi", "gigi rendah", "tidak bisa pindah range", "pressure switch", "splitter"),
             "jawab": {("gigi", "Susah masuk gigi"): 1, ("gigi", "Tidak ada"): -1},
             "cek": ["Cek tekanan angin ke transmisi & saringan angin range",
                     "Cek pressure switch / DIN connector dan solenoid range",
                     "Cek silinder range & shift booster"],
             "part": ["pressure switch", "range gear", "solenoid valve", "shift booster", "din joint"],
             "spn": [], "manual": "range gear transmisi"},
            {"id": "oli_gearbox", "nama": "Oli gearbox bocor / bunyi dengung (seal, bearing)",
             "pemicu": _rx("oli gearbox", "bocor gearbox", "dengung", "bunyi transmisi", "oli transmisi", "rembes"),
             "jawab": {("kopling", "Kopling bergetar / bunyi"): 1, ("gigi", "Tidak ada"): 1},
             "cek": ["Cek seal input / output shaft & tutup input shaft",
                     "Cek level oli dan serbuk logam di drain plug",
                     "Cek bearing poros (bunyi berubah saat kopling diinjak?)"],
             "part": ["shaft sealing ring", "input shaft", "output shaft", "tapered roller bearing", "oil seal"],
             "spn": [], "manual": "bocor oli transmisi"},
        ],
    },
    "suspensi_sasis": {
        "label": "Suspensi & sasis (kolong)",
        "pemicu": _rx("kolong", "suspensi", "per", "per daun", "karet", "rubber", "v.?stay", "thrust rod",
                      "torque rod", "stabilizer", "stabiliser", "balance shaft", "bogie", "shock", "sok",
                      "limbung", "miring", "gluduk", "bracket", "hanger", "pen per"),
        "tanya": [
            {"id": "bunyi", "teks": "Bunyinya seperti apa?",
             "opsi": ["Bunyi 'duk' saat jalan jelek / rem", "Bunyi berderit / gesek", "Tidak ada bunyi, unit miring / limbung"],
             "otomatis": {"Bunyi 'duk' saat jalan jelek / rem": _rx("duk", "gluduk", "jeduk", "kletek"),
                          "Bunyi berderit / gesek": _rx("derit", "berderit", "gesek", "ngik"),
                          "Tidak ada bunyi, unit miring / limbung": _rx("miring", "limbung", "amblas", "oleng", "turun sebelah")}},
            {"id": "muatan", "teks": "Kondisi muatan saat gejala muncul?",
             "opsi": ["Bermuatan penuh / lebih", "Kosong", "Sama saja"],
             "otomatis": {"Bermuatan penuh / lebih": _rx("muatan", "bermuatan", "overload", "beban penuh"),
                          "Kosong": _rx("kosongan", "tanpa muatan")}},
        ],
        "sebab": [
            {"id": "rubber_mount", "nama": "Karet dudukan suspensi (rubber mount) pecah / retak",
             "pemicu": _rx("karet", "rubber", "dudukan per", "bushing", "duk", "gluduk", "karet per"),
             "jawab": {("bunyi", "Bunyi 'duk' saat jalan jelek / rem"): 3, ("muatan", "Bermuatan penuh / lebih"): 1},
             "cek": ["Visual: karet retak/pecah, celah antara per dan dudukan, karet terlepas",
                     "Cek kekencangan baut U & baut dudukan",
                     "Cek bracket per belakang ikut retak (sering diganti bersamaan)"],
             "part": ["suspension rubber mount", "rubber support", "rubber mount", "rubber seat"],
             "spn": [], "manual": "karet dudukan suspensi"},
            {"id": "thrust_rod", "nama": "V-stay / batang torsi (thrust rod) patah atau bushing aus",
             "pemicu": _rx("v.?stay", "thrust rod", "torque rod", "batang penahan", "gardan mundur", "as belakang geser", "bushing"),
             "jawab": {("bunyi", "Bunyi 'duk' saat jalan jelek / rem"): 2, ("muatan", "Bermuatan penuh / lebih"): 2},
             "cek": ["Cek bushing V-stay & straight rod (oval, karet lepas)",
                     "Cek baut dudukan V-stay ke gardan & rangka (kendor/patah → gardan bergeser)",
                     "Cek kelurusan gardan belakang vs sasis"],
             "part": ["v-type thrust rod", "straight thrust rod", "thrust rod", "torque rod"],
             "spn": [], "manual": "thrust rod suspensi"},
            {"id": "bracket_per", "nama": "Bracket / hanger per patah, pen per aus",
             "pemicu": _rx("bracket", "dudukan per", "hanger", "pen per", "patah", "gantungan per"),
             "jawab": {("bunyi", "Bunyi 'duk' saat jalan jelek / rem"): 2, ("bunyi", "Bunyi berderit / gesek"): 1},
             "cek": ["Cek retak di bracket depan/belakang per daun & baut ke sasis",
                     "Cek pen per & bushing (kelonggaran, nipel gemuk)",
                     "Cek lug pengangkat per depan"],
             "part": ["bracket of front spring", "plate spring bracket", "spring pin", "spring bracket", "lifting lug"],
             "spn": [], "manual": "bracket per daun"},
            {"id": "per_daun", "nama": "Per daun patah / lemah (unit miring, amblas)",
             "pemicu": _rx("per patah", "per lemah", "miring", "limbung", "amblas", "per daun", "turun sebelah"),
             "jawab": {("bunyi", "Tidak ada bunyi, unit miring / limbung"): 3, ("muatan", "Bermuatan penuh / lebih"): 1},
             "cek": ["Ukur tinggi sasis kiri vs kanan; cek daun per patah / center bolt",
                     "Cek klem per & baut U",
                     "Cek dudukan per & plat penekan"],
             "part": ["leaf spring", "spring assembly", "spring pressure plate", "center bolt", "u-bolt"],
             "spn": [], "manual": "per daun"},
            {"id": "stabilizer", "nama": "Stabilizer & bushing / hanger stabilizer",
             "pemicu": _rx("stabilizer", "stabiliser", "limbung", "oleng", "goyang", "ayun"),
             "jawab": {("bunyi", "Tidak ada bunyi, unit miring / limbung"): 2, ("bunyi", "Bunyi berderit / gesek"): 2},
             "cek": ["Cek batang stabilizer bengkok/patah",
                     "Cek bushing & hanger stabilizer (aus, baut patah, klem lepas)",
                     "Cek backing plate & washer dudukan"],
             "part": ["stabilizer bar", "stabilizer bar bearing", "stabilizer rod hanger", "stabilizer"],
             "spn": [], "manual": "stabilizer"},
            {"id": "balance_shaft", "nama": "Bearing / seal balance shaft (bogie tengah)",
             "pemicu": _rx("balance shaft", "bogie", "as tengah", "kolong bunyi", "seal balance"),
             "jawab": {("bunyi", "Bunyi berderit / gesek"): 2, ("bunyi", "Bunyi 'duk' saat jalan jelek / rem"): 1},
             "cek": ["Cek kelonggaran balance shaft (goyang per bogie)",
                     "Cek rembesan gemuk/oli di seal balance shaft",
                     "Cek bearing & bushing balance shaft"],
             "part": ["balance shaft bearing", "balance shaft", "shaft sealing ring", "balance shaft housing"],
             "spn": [], "manual": "balance shaft"},
            {"id": "shock", "nama": "Shock absorber bocor / mati",
             "pemicu": _rx("shock", "sok", "shockbreaker", "mengayun", "memantul", "bouncing"),
             "jawab": {("bunyi", "Tidak ada bunyi, unit miring / limbung"): 1, ("muatan", "Kosong"): 1},
             "cek": ["Cek rembesan oli di tabung shock & karet dudukan",
                     "Tes pantul: unit terus mengayun = shock mati",
                     "Cek baut dudukan shock"],
             "part": ["shock absorber", "lateral stability shock absorber"],
             "spn": [], "manual": "shock absorber"},
        ],
    },
    "gardan_penggerak": {
        "label": "Gardan & penggerak",
        "pemicu": _rx("gardan", "differential", "diff", "kopel", "propeller", "universal joint", "cross joint",
                      "as roda", "axle", "nanas", "pinion", "hub", "naf", "reducer", "planetary", "dengung",
                      "inter.?axle", "final drive", "roda tidak berputar"),
        "tanya": [
            {"id": "kapan_g", "teks": "Kapan bunyi / getarnya terasa?",
             "opsi": ["Saat gas (menarik beban)", "Saat lepas gas / meluncur", "Kecepatan tinggi saja", "Saat belok"],
             "otomatis": {"Saat gas (menarik beban)": _rx("saat gas", "digas", "menarik", "tarik beban"),
                          "Saat lepas gas / meluncur": _rx("lepas gas", "meluncur", "coasting"),
                          "Kecepatan tinggi saja": _rx("kecepatan tinggi", "kencang", "di atas"),
                          "Saat belok": _rx("belok", "berbelok", "menikung")}},
            {"id": "jenis_g", "teks": "Apa yang dirasakan?",
             "opsi": ["Dengung / mendengung", "Getar di lantai kabin", "Bunyi kletek / patah", "Roda tidak berputar"],
             "otomatis": {"Dengung / mendengung": _rx("dengung", "mendengung", "ngung"),
                          "Getar di lantai kabin": _rx("getar", "bergetar", "vibrasi"),
                          "Bunyi kletek / patah": _rx("kletek", "patah", "klotok", "bunyi keras"),
                          "Roda tidak berputar": _rx("tidak berputar", "tidak jalan", "roda diam", "tidak bergerak")}},
        ],
        "sebab": [
            {"id": "kopel", "nama": "Kopel / universal joint / bearing tengah aus",
             "pemicu": _rx("kopel", "propeller", "universal joint", "cross joint", "as kopel", "getar", "bearing tengah", "gantungan kopel", "spline"),
             "jawab": {("jenis_g", "Getar di lantai kabin"): 3, ("kapan_g", "Kecepatan tinggi saja"): 2, ("jenis_g", "Bunyi kletek / patah"): 1},
             "cek": ["Cek kelonggaran cross joint & spline kopel (goyang dengan tangan)",
                     "Cek bearing gantungan tengah & karetnya",
                     "Cek keseimbangan kopel (bobot balancing lepas, kopel bengkok)"],
             "part": ["drive shaft", "universal joint", "transmission shaft", "center bearing", "cross joint", "interaxle drive shaft"],
             "spn": [], "manual": "poros kopel"},
            {"id": "nanas", "nama": "Gigi pinion / nanas gardan aus atau setelan backlash",
             "pemicu": _rx("dengung", "nanas", "pinion", "gardan bunyi", "final drive", "crown", "backlash"),
             "jawab": {("jenis_g", "Dengung / mendengung"): 3, ("kapan_g", "Saat gas (menarik beban)"): 1, ("kapan_g", "Saat lepas gas / meluncur"): 1},
             "cek": ["Cek level & serbuk logam di oli gardan",
                     "Cek bunyi berubah antara gas dan lepas gas (pinion vs bearing)",
                     "Ukur backlash & pola kontak gigi nanas; cek bearing pinion"],
             "part": ["final drive", "pinion", "crown wheel", "differential", "drive gear", "bevel gear", "differential bearing"],
             "spn": [], "manual": "gardan dengung"},
            {"id": "diff_lock", "nama": "Inter-axle / differential lock (silinder, garpu, poros antar gardan)",
             "pemicu": _rx("diff lock", "inter.?axle", "pembagi", "lock tidak fungsi", "lampu diff", "kunci gardan", "poros antar"),
             "jawab": {("jenis_g", "Roda tidak berputar"): 2, ("kapan_g", "Saat belok"): 1, ("jenis_g", "Bunyi kletek / patah"): 1},
             "cek": ["Cek angin ke silinder diff lock & saklar / lampu indikator",
                     "Cek garpu & lengan silinder diff lock (patah, macet)",
                     "Cek poros antar gardan (inter-axle shaft) & flange"],
             "part": ["inter-axle differential lock", "differential lock cylinder", "inter-axle transmission shaft", "differential lock"],
             "spn": [], "manual": "differential lock"},
            {"id": "hub", "nama": "Wheel end: bearing roda / hub reducer / planetary",
             "pemicu": _rx("hub", "naf", "bearing roda", "laher roda", "roda panas", "reducer", "planetary", "roda bunyi"),
             "jawab": {("kapan_g", "Saat belok"): 2, ("jenis_g", "Bunyi kletek / patah"): 1, ("jenis_g", "Dengung / mendengung"): 1},
             "cek": ["Cek panas hub tiap roda setelah jalan (bearing)",
                     "Cek kelonggaran bearing roda (goyang ban atas-bawah)",
                     "Cek oli hub reducer & serbuk logam; planetary gear"],
             "part": ["wheel hub", "wheel bearing", "wheel rim reducer", "planetary gear", "hub reducer", "hub oil seal"],
             "spn": [], "manual": "bearing roda"},
            {"id": "as_roda", "nama": "As roda (axle shaft) / flange patah",
             "pemicu": _rx("as roda patah", "axle shaft", "as patah", "tidak berputar", "flange", "as roda"),
             "jawab": {("jenis_g", "Roda tidak berputar"): 3, ("jenis_g", "Bunyi kletek / patah"): 2, ("kapan_g", "Saat gas (menarik beban)"): 1},
             "cek": ["Lepas flange as roda: cek spline & patahan",
                     "Cek baut flange & pin dowel",
                     "Cek sisa patahan di dalam gardan sebelum pasang baru"],
             "part": ["axle shaft", "half shaft", "flange assembly", "half axle"],
             "spn": [], "manual": "as roda"},
            {"id": "oli_gardan", "nama": "Oli gardan bocor (seal hub, seal pinion, paking rumah gardan)",
             "pemicu": _rx("oli gardan", "bocor gardan", "seal roda", "rembes roda", "oli di tromol", "seal pinion"),
             "jawab": {("jenis_g", "Dengung / mendengung"): 1},
             "cek": ["Cek rembesan di seal hub (oli masuk ke tromol → rem selip)",
                     "Cek seal pinion / flange & breather gardan buntu",
                     "Cek paking rumah gardan & level oli"],
             "part": ["oil seal", "hub oil seal", "differential housing gasket", "breather", "shaft sealing ring"],
             "spn": [], "manual": "seal gardan"},
        ],
    },
    "kelistrikan": {
        "label": "Kelistrikan",
        "pemicu": _rx("aki", "accu", "battery", "starter", "dinamo", "alternator", "listrik", "lampu",
                      "kabel", "sekring", "fuse", "relay", "konslet", "soket", "klakson", "wiper", "spido",
                      "panel", "dashboard", "buzzer", "kamera", "kunci kontak", "tekor", "jumper"),
        "tanya": [
            {"id": "starter", "teks": "Saat kunci diputar ke start, apa yang terjadi?",
             "opsi": ["Tidak ada reaksi sama sekali", "Bunyi klik / dinamo lemah", "Mesin berputar tapi tidak hidup", "Bukan masalah starter"],
             "otomatis": {"Tidak ada reaksi sama sekali": _rx("tidak ada reaksi", "diam", "tidak bereaksi", "mati total"),
                          "Bunyi klik / dinamo lemah": _rx("klik", "lemah", "cetek"),
                          "Mesin berputar tapi tidak hidup": _rx("berputar", "ngayun", "tidak hidup", "tidak mau hidup")}},
            {"id": "aki", "teks": "Bagaimana kondisi aki & pengisiannya?",
             "opsi": ["Aki sering tekor / harus dijumper", "Lampu aki (pengisian) menyala", "Aki normal"],
             "otomatis": {"Aki sering tekor / harus dijumper": _rx("tekor", "jumper", "drop", "soak"),
                          "Lampu aki (pengisian) menyala": _rx("lampu aki", "pengisian", "charging")}},
        ],
        "sebab": [
            {"id": "aki_lemah", "nama": "Aki lemah / terminal & kabel massa kotor",
             "pemicu": _rx("aki", "accu", "tekor", "jumper", "drop", "soak", "terminal"),
             "jawab": {("starter", "Bunyi klik / dinamo lemah"): 3, ("aki", "Aki sering tekor / harus dijumper"): 3,
                       ("starter", "Tidak ada reaksi sama sekali"): 2},
             "cek": ["Ukur tegangan aki (≥ 24,5 V diam; saat start jangan < 19 V) & tes beban",
                     "Bersihkan & kencangkan terminal, kabel massa ke sasis / mesin",
                     "Cek arus bocor saat kunci off (parasitic drain)"],
             "part": ["battery", "battery cable", "battery terminal", "ground cable"],
             "spn": [168], "manual": "aki tegangan rendah"},
            {"id": "alternator", "nama": "Alternator / pengisian lemah (sikat, regulator, belt)",
             "pemicu": _rx("alternator", "dinamo ampere", "pengisian", "lampu aki", "charging", "belt", "van belt"),
             "jawab": {("aki", "Lampu aki (pengisian) menyala"): 3, ("aki", "Aki sering tekor / harus dijumper"): 1},
             "cek": ["Ukur tegangan pengisian saat mesin hidup (27,5–28,5 V)",
                     "Cek belt kendor / putus & puli alternator",
                     "Cek sikat, regulator, dioda; kabel B+ ke aki"],
             "part": ["generator", "alternator", "v-ribbed belt", "belt", "tensioner"],
             "spn": [167], "manual": "tegangan pengisian alternator"},
            {"id": "starter", "nama": "Motor starter / relay starter / kunci kontak",
             "pemicu": _rx("starter", "dinamo starter", "kunci kontak", "relay starter", "klik", "cetek", "tidak bereaksi"),
             "jawab": {("starter", "Tidak ada reaksi sama sekali"): 3, ("starter", "Bunyi klik / dinamo lemah"): 2, ("aki", "Aki normal"): 1},
             "cek": ["Cek tegangan sampai ke terminal 50 starter saat kunci start",
                     "Cek relay starter, saklar netral, kunci kontak",
                     "Cek solenoid & bendix starter (klik tanpa putar)"],
             "part": ["starter motor", "starter", "starter relay", "key switch", "ignition switch", "neutral switch"],
             "spn": [], "manual": "motor starter"},
            {"id": "putar_tak_hidup", "nama": "Mesin berputar tapi tidak hidup (bahan bakar, pemanas, sensor crank, sekring ECU)",
             "pemicu": _rx("berputar tapi", "tidak mau hidup", "ngayun", "tidak hidup"),
             "jawab": {("starter", "Mesin berputar tapi tidak hidup"): 3},
             "cek": ["Cek sekring & relay ECU / pompa solar; tegangan ECU saat start",
                     "Cek solar sampai rail (bleeding), busi pijar saat dingin",
                     "Cek sinyal sensor crankshaft / camshaft (cari_kode_kesalahan)"],
             "part": ["glow plug", "crankshaft sensor", "fuel filter", "fuse", "relay", "feed pump"],
             "spn": [636, 723], "manual": "mesin tidak hidup"},
            {"id": "kabel_bodi", "nama": "Kabel bodi / soket / sekring / ground (lampu mati, konslet, kedip)",
             "pemicu": _rx("lampu mati", "kabel", "soket", "konslet", "sekring", "fuse", "putus", "kedip", "lampu"),
             "jawab": {("starter", "Bukan masalah starter"): 2, ("aki", "Aki normal"): 1},
             "cek": ["Cek sekring & relay rangkaian terkait (kotak sekring kabin)",
                     "Cek soket korosi / longgar & kabel tergesek di jalur",
                     "Cek ground bodi & tegangan di beban"],
             "part": ["wiring harness", "connector", "fuse", "relay", "lamp", "combination switch"],
             "spn": [], "manual": "diagram kelistrikan"},
            {"id": "instrumen", "nama": "Panel instrumen / buzzer / kamera / saklar",
             "pemicu": _rx("speedometer", "spido", "panel mati", "dashboard", "buzzer", "alarm mundur", "kamera", "saklar", "klakson", "wiper"),
             "jawab": {("starter", "Bukan masalah starter"): 2},
             "cek": ["Cek sekring & soket unit terkait (panel, buzzer, kamera)",
                     "Cek sinyal masuk (sensor kecepatan untuk spido, saklar mundur untuk buzzer)",
                     "Cek unit (ganti uji) bila tegangan & sinyal masuk normal"],
             "part": ["instrument cluster", "combination switch", "reversing buzzer", "rear camera", "toggle switch", "sensor", "wiper motor", "horn"],
             "spn": [84], "manual": "panel instrumen"},
        ],
    },
    "kemudi": {
        "label": "Sistem kemudi",
        "pemicu": _rx("stir", "setir", "steering", "kemudi", "power steering", "tie rod", "ball joint",
                      "king pin", "drag link", "speleng", "oleng"),
        "tanya": [
            {"id": "stir", "teks": "Apa gejala di setirnya?",
             "opsi": ["Setir berat", "Setir goyang / getar", "Setir oleng / tidak lurus", "Bunyi saat belok"],
             "otomatis": {"Setir berat": _rx("berat"),
                          "Setir goyang / getar": _rx("goyang", "getar", "bergetar", "shimmy"),
                          "Setir oleng / tidak lurus": _rx("oleng", "tidak lurus", "narik", "lari", "speleng", "longgar"),
                          "Bunyi saat belok": _rx("bunyi")}},
        ],
        "sebab": [
            {"id": "ps", "nama": "Pompa / oli power steering, selang, steering gear",
             "pemicu": _rx("setir berat", "stir berat", "power steering", "oli ps", "berat saat belok", "berat"),
             "jawab": {("stir", "Setir berat"): 3, ("stir", "Bunyi saat belok"): 1},
             "cek": ["Cek level & kebocoran oli PS, saringan tangki PS",
                     "Cek tekanan pompa PS & belt penggerak",
                     "Cek steering gear (bocor internal) & angin di sistem"],
             "part": ["power steering pump", "steering pump", "steering oil", "power steering hose", "steering gear"],
             "spn": [], "manual": "power steering berat"},
            {"id": "tie_rod", "nama": "Tie rod / drag link / ball joint / king pin longgar",
             "pemicu": _rx("oleng", "goyang", "tie rod", "ball joint", "king pin", "longgar", "speleng", "drag link", "tidak lurus"),
             "jawab": {("stir", "Setir oleng / tidak lurus"): 3, ("stir", "Setir goyang / getar"): 2},
             "cek": ["Goyang roda kiri-kanan (tie rod / ball joint) & atas-bawah (king pin / bearing)",
                     "Cek kelonggaran drag link & lengan pitman",
                     "Cek toe-in & camber setelah komponen diganti"],
             "part": ["tie rod", "ball joint", "king pin", "drag link", "steering pull rod", "knuckle"],
             "spn": [], "manual": "tie rod ball joint"},
            {"id": "roda", "nama": "Bearing roda / balancing ban / velg",
             "pemicu": _rx("getar", "ban benjol", "balancing", "velg", "peleg", "bearing roda"),
             "jawab": {("stir", "Setir goyang / getar"): 3},
             "cek": ["Cek bearing roda depan (kelonggaran, panas)",
                     "Cek ban benjol / aus tidak rata & balancing",
                     "Cek velg peyang & baut roda"],
             "part": ["wheel bearing", "wheel hub", "tyre", "wheel assembly", "rim"],
             "spn": [], "manual": "roda bergetar"},
            {"id": "gearbox_stir", "nama": "Steering gear / kolom kemudi / cross joint kemudi",
             "pemicu": _rx("gearbox stir", "bunyi stir", "steering gear", "bunyi belok", "kolom", "cross joint stir"),
             "jawab": {("stir", "Bunyi saat belok"): 3, ("stir", "Setir oleng / tidak lurus"): 1},
             "cek": ["Cek cross joint & spline kolom kemudi (kelonggaran, bunyi)",
                     "Cek setelan sektor steering gear & kebocoran",
                     "Cek baut dudukan steering gear ke sasis"],
             "part": ["steering gear", "steering column", "cross joint", "steering shaft"],
             "spn": [], "manual": "steering gear"},
        ],
    },
    "kabin_ac": {
        "label": "Kabin & AC",
        "pemicu": _rx("ac", "air conditioner", "kabin", "cabin", "blower", "freon", "kondensor", "evaporator",
                      "pintu", "kaca", "wiper", "spion", "jok", "power window", "dingin"),
        "tanya": [
            {"id": "ac", "teks": "Bagaimana gejala AC-nya?",
             "opsi": ["AC tidak dingin sama sekali", "AC kurang dingin / hidup-mati", "Blower tidak berputar", "Bukan masalah AC"],
             "otomatis": {"AC tidak dingin sama sekali": _rx("tidak dingin", "gak dingin", "nggak dingin", "panas"),
                          "AC kurang dingin / hidup-mati": _rx("kurang dingin", "hidup.?mati", "kadang dingin"),
                          "Blower tidak berputar": _rx("blower", "angin tidak keluar", "tidak ada angin", "kipas ac")}},
        ],
        "sebab": [
            {"id": "kompresor_ac", "nama": "Kompresor AC / magnetic clutch / belt",
             "pemicu": _rx("ac tidak dingin", "kompresor ac", "magnet", "clutch ac", "ac mati", "tidak dingin"),
             "jawab": {("ac", "AC tidak dingin sama sekali"): 3, ("ac", "AC kurang dingin / hidup-mati"): 1},
             "cek": ["Cek magnetic clutch menempel saat AC on (tegangan ke clutch, relay, saklar tekanan)",
                     "Cek belt kompresor & tekanan freon sisi rendah/tinggi",
                     "Cek kompresor (bunyi, bocor oli di seal)"],
             "part": ["air condition compressor", "air conditioning compressor", "compressor clutch", "ac compressor", "compressor"],
             "spn": [], "manual": "kompresor ac"},
            {"id": "freon", "nama": "Freon kurang / kondensor kotor / expansion valve / receiver drier",
             "pemicu": _rx("kurang dingin", "freon", "bocor", "kondensor", "hidup.?mati", "expansion", "dryer"),
             "jawab": {("ac", "AC kurang dingin / hidup-mati"): 3, ("ac", "AC tidak dingin sama sekali"): 1},
             "cek": ["Cek tekanan freon & cari kebocoran (UV / sabun) di kondensor, selang, seal",
                     "Bersihkan kondensor & pastikan kipas kondensor bekerja",
                     "Cek expansion valve beku / buntu dan receiver drier"],
             "part": ["condenser", "expansion valve", "receiver drier", "accumulator", "ac hose", "condenser fan"],
             "spn": [], "manual": "freon ac"},
            {"id": "blower", "nama": "Blower / resistor / filter kabin / evaporator kotor",
             "pemicu": _rx("blower", "angin ac", "kipas ac", "filter kabin", "evaporator", "bau"),
             "jawab": {("ac", "Blower tidak berputar"): 3, ("ac", "AC kurang dingin / hidup-mati"): 1},
             "cek": ["Cek sekring, relay & resistor blower; saklar kecepatan",
                     "Cek motor blower (macet, sikat)",
                     "Cek filter kabin & evaporator kotor / beku"],
             "part": ["blower", "blower motor", "evaporator", "cabin filter", "resistor"],
             "spn": [], "manual": "blower ac"},
            {"id": "kabin_goyang", "nama": "Suspensi kabin (shock kabin, air bag kabin, kunci kabin)",
             "pemicu": _rx("kabin goyang", "kabin bunyi", "shock kabin", "air bag kabin", "kabin miring", "kabin turun", "kunci kabin", "kabin"),
             "jawab": {("ac", "Bukan masalah AC"): 2},
             "cek": ["Cek shock & pegas / air bag kabin (bocor, karet pecah)",
                     "Cek kunci kabin & sensor kabin terkunci",
                     "Cek bushing & baut dudukan kabin"],
             "part": ["cab shock absorber", "cab suspension", "cab air spring", "cab coil spring damper", "cab lock", "cabin"],
             "spn": [], "manual": "suspensi kabin"},
            {"id": "pintu_kaca", "nama": "Pintu / kaca / wiper / spion / jok",
             "pemicu": _rx("pintu", "kaca", "wiper", "spion", "engsel", "power window", "jok", "seat"),
             "jawab": {("ac", "Bukan masalah AC"): 2},
             "cek": ["Cek mekanisme (regulator kaca, engsel, kunci) & motor penggerak",
                     "Cek sekring, saklar & soket bila elektrik",
                     "Cek karet / seal bila bocor air"],
             "part": ["door lock", "window regulator", "wiper motor", "wiper blade", "rearview mirror", "seat", "door handle"],
             "spn": [], "manual": "pintu kabin"},
        ],
    },
    "scr_urea": {
        "label": "Gas buang & SCR / urea",
        "pemicu": _rx("urea", "adblue", "scr", "nox", "dcu", "knalpot", "exhaust", "gas buang", "muffler", "derate"),
        "tanya": [
            {"id": "scr", "teks": "Apa yang tampak di panel?",
             "opsi": ["Lampu SCR / urea menyala", "Tenaga dibatasi (derate)", "Level urea tidak terbaca", "Tidak ada lampu"],
             "otomatis": {"Lampu SCR / urea menyala": _rx("lampu scr", "lampu urea", "lampu nox", "indikator urea"),
                          "Tenaga dibatasi (derate)": _rx("derate", "tenaga dibatasi", "limp", "rpm dibatasi"),
                          "Level urea tidak terbaca": _rx("level urea", "tidak terbaca", "meter urea", "urea kosong")}},
        ],
        "sebab": [
            {"id": "urea_tangki", "nama": "Kualitas / level urea & sensor tangki urea",
             "pemicu": _rx("urea", "adblue", "tangki urea", "level urea", "kualitas urea"),
             "jawab": {("scr", "Level urea tidak terbaca"): 3, ("scr", "Lampu SCR / urea menyala"): 2},
             "cek": ["Cek urea asli (refraktometer 32,5%) & level; kuras bila tercemar",
                     "Cek sensor level / kualitas / suhu tangki urea (baca DTC)",
                     "Cek pemanas tangki & saringan hisap urea"],
             "part": ["urea tank", "urea level sensor", "urea quality sensor", "urea tank sensor", "urea filter"],
             "spn": [1761, 3031, 3516], "manual": "sensor tangki urea"},
            {"id": "urea_pompa", "nama": "Pompa / injektor urea buntu (kristal urea)",
             "pemicu": _rx("pompa urea", "dosing", "nozzle urea", "kristal", "injektor urea"),
             "jawab": {("scr", "Lampu SCR / urea menyala"): 2, ("scr", "Tenaga dibatasi (derate)"): 2},
             "cek": ["Tes tekanan pompa urea (build-up & hold) dari diagnostik",
                     "Cek nozzle / injektor urea tersumbat kristal; bersihkan dengan air hangat",
                     "Cek saringan pompa urea & selang pemanas"],
             "part": ["urea pump", "urea injector", "dosing module", "urea nozzle", "solenoid valve", "urea pump filter"],
             "spn": [4334, 4375, 4376], "manual": "pompa urea tekanan"},
            {"id": "nox", "nama": "Sensor NOx / suhu gas buang / DCU (derate)",
             "pemicu": _rx("nox", "sensor nox", "dcu", "suhu gas buang", "efisiensi", "katalis"),
             "jawab": {("scr", "Tenaga dibatasi (derate)"): 3, ("scr", "Lampu SCR / urea menyala"): 1},
             "cek": ["Baca kode kesalahan DCU / SCR (cari_kode_kesalahan)",
                     "Cek sensor NOx hulu/hilir & sensor suhu gas buang (nilai, kabel, soket)",
                     "Cek efisiensi katalis SCR & kebocoran gas buang sebelum sensor"],
             "part": ["nox sensor", "exhaust temperature sensor", "dcu", "after-treatment", "scr catalyst"],
             "spn": [3226, 3216, 4360, 4363], "manual": "sensor nox"},
            {"id": "knalpot", "nama": "Knalpot / pipa buang retak atau bocor",
             "pemicu": _rx("knalpot", "exhaust", "pipa buang", "retak", "bocor gas buang", "bunyi knalpot", "muffler"),
             "jawab": {("scr", "Tidak ada lampu"): 2},
             "cek": ["Cek retak di pipa buang atas (upper exhaust pipe) & sambungan flexible",
                     "Cek klem & paking knalpot, dudukan muffler",
                     "Cek bocor sebelum sensor NOx (memicu kode SCR)"],
             "part": ["exhaust pipe", "flexible pipe", "muffler", "exhaust gasket", "exhaust brake"],
             "spn": [], "manual": "pipa knalpot"},
        ],
    },
}

# Kartu pemilih sistem bila keluhan tak menyebut sistem mana pun.
_PILIH_SISTEM_TEKS = "Gejalanya paling terasa di bagian mana?"
_PILIH_SISTEM_OPSI: dict[str, str] = {
    "Mesin / tenaga / asap": "mesin",
    "Rem / angin": "angin_rem",
    "Transmisi / kopling": "transmisi_kopling",
    "Kolong / suspensi": "suspensi_sasis",
    "Gardan / roda / kopel": "gardan_penggerak",
    "Kelistrikan / starter / aki": "kelistrikan",
    "Setir / kemudi": "kemudi",
    "Kabin / AC": "kabin_ac",
    "Gas buang / urea / SCR": "scr_urea",
}
_SISTEM_ALIAS = {
    "mesin": "mesin", "engine": "mesin", "bahan bakar": "mesin",
    "rem": "angin_rem", "angin": "angin_rem", "brake": "angin_rem", "angin_rem": "angin_rem",
    "transmisi": "transmisi_kopling", "kopling": "transmisi_kopling", "gearbox": "transmisi_kopling",
    "transmisi_kopling": "transmisi_kopling",
    "suspensi": "suspensi_sasis", "sasis": "suspensi_sasis", "kolong": "suspensi_sasis",
    "suspensi_sasis": "suspensi_sasis",
    "gardan": "gardan_penggerak", "penggerak": "gardan_penggerak", "gardan_penggerak": "gardan_penggerak",
    "kelistrikan": "kelistrikan", "listrik": "kelistrikan", "elektrik": "kelistrikan",
    "kemudi": "kemudi", "setir": "kemudi", "stir": "kemudi",
    "kabin": "kabin_ac", "ac": "kabin_ac", "kabin_ac": "kabin_ac",
    "scr": "scr_urea", "urea": "scr_urea", "gas buang": "scr_urea", "scr_urea": "scr_urea",
}


def sistem_tersedia() -> list[dict]:
    return [{"kode": k, "label": v["label"]} for k, v in _SISTEM.items()]


# ── Klasifikasi & parsing ────────────────────────────────────────────────────
def _norm(t: str) -> str:
    return re.sub(r"\s+", " ", (t or "").strip().lower())


def _skor_sistem(teks: str) -> list[tuple[float, str]]:
    """Skor tiap sistem dari pemicu sistem + pemicu daun (daun lebih spesifik
    → bobot lebih tinggi). Urut turun."""
    hasil = []
    for kode, s in _SISTEM.items():
        sk = sum(1.0 for rx in s["pemicu"] if rx.search(teks))
        for d in s["sebab"]:
            sk += sum(1.5 for rx in d["pemicu"] if rx.search(teks))
        if sk > 0:
            hasil.append((sk, kode))
    hasil.sort(reverse=True)
    return hasil


def _tebak_sistem(teks: str, sistem_arg: str | None) -> tuple[str | None, list[tuple[float, str]]]:
    if sistem_arg:
        a = _norm(sistem_arg)
        if a in _SISTEM_ALIAS:
            return _SISTEM_ALIAS[a], []
        for k, v in _SISTEM_ALIAS.items():
            if re.search(r"(?<!\w)" + re.escape(k) + r"(?!\w)", a):
                return v, []
    urut = _skor_sistem(teks)
    if not urut:
        return None, []
    # Sistem teratas harus unggul jelas; kalau seri, biar user yang memilih.
    if len(urut) > 1 and urut[0][0] < urut[1][0] * 1.25 and urut[0][0] < 4:
        return None, urut
    return urut[0][1], urut


def _teks_jawaban(jawaban) -> str:
    if jawaban is None:
        return ""
    if isinstance(jawaban, str):
        return jawaban
    if isinstance(jawaban, dict):
        return "\n".join(f"{k} {v}" for k, v in jawaban.items() if v is not None)
    if isinstance(jawaban, (list, tuple)):
        return "\n".join(_teks_jawaban(j) for j in jawaban)
    return str(jawaban)


def _parse_jawaban(sistem: str, teks_jawab: str) -> dict[str, str]:
    """Teks jawaban kartu → {id_tanya: opsi}. Mencocokkan OPSI (persis, tanpa
    peduli huruf); yang tak dikenali diabaikan."""
    tj = _norm(teks_jawab)
    if not tj:
        return {}
    out: dict[str, str] = {}
    for q in _SISTEM[sistem]["tanya"]:
        for opsi in q["opsi"]:
            if _norm(opsi) in tj:
                out[q["id"]] = opsi
                break
    return out


def _jawab_otomatis(sistem: str, teks: str) -> dict[str, str]:
    out: dict[str, str] = {}
    for q in _SISTEM[sistem]["tanya"]:
        for opsi, rxs in (q.get("otomatis") or {}).items():
            if any(rx.search(teks) for rx in rxs):
                out[q["id"]] = opsi
                break
    return out


def _pilih_sistem_dari_jawaban(teks_jawab: str) -> str | None:
    tj = _norm(teks_jawab)
    for opsi, kode in _PILIH_SISTEM_OPSI.items():
        if _norm(opsi) in tj:
            return kode
    return None


# ── Pagar tanya-ulang ────────────────────────────────────────────────────────
def _kunci(username: str, keluhan: str, sistem: str) -> str:
    return f"{username or ''}|{sistem}|{_norm(keluhan)[:80]}"


def _sudah_tanya(kunci: str) -> bool:
    now = time.monotonic()
    with _lock:
        for k in [k for k, t in _SUDAH_TANYA.items() if now - t > _TANYA_TTL]:
            _SUDAH_TANYA.pop(k, None)
        return kunci in _SUDAH_TANYA


def _catat_tanya(kunci: str) -> None:
    with _lock:
        if len(_SUDAH_TANYA) >= _TANYA_MAKS:
            _SUDAH_TANYA.pop(next(iter(_SUDAH_TANYA)), None)
        _SUDAH_TANYA[kunci] = time.monotonic()


def _reset() -> None:
    """TESTS ONLY."""
    with _lock:
        _SUDAH_TANYA.clear()


# ── Peringkat & bukti ────────────────────────────────────────────────────────
def _skor_sebab(sistem: str, teks: str, jawab: dict[str, str]) -> list[tuple[float, dict]]:
    hasil = []
    for d in _SISTEM[sistem]["sebab"]:
        sk = 0.0
        hit = sum(1 for rx in d["pemicu"] if rx.search(teks))
        sk += _BOBOT_PEMICU * min(hit, 2)
        for (qid, opsi), bobot in d["jawab"].items():
            if jawab.get(qid) == opsi:
                sk += bobot
        hasil.append((sk, d))
    return hasil


def _bukti_klaim(d: dict) -> list[dict]:
    try:
        return warranty_profil.profil_untuk_kata(d.get("part") or [], batas=_BATAS_PART)
    except Exception:
        return []


def _kode_untuk(d: dict) -> list[dict]:
    out: list[dict] = []
    for spn in d.get("spn") or []:
        try:
            rows = dtc_codes.search_spn_fmi(spn, None, limit=_BATAS_KODE)
        except Exception:
            rows = []
        for r in rows:
            if not (r.get("kode") or r.get("deskripsi")):
                continue
            out.append({"kode": r.get("kode"), "spn": r.get("spn"), "fmi": r.get("fmi"),
                        "unit": r.get("unit"), "deskripsi": (r.get("deskripsi") or "")[:120]})
    return out[:_BATAS_KODE * 2]


def _manual_untuk(d: dict) -> dict | None:
    topik = d.get("manual")
    if not topik:
        return None
    try:
        if not manual_teks.available():
            return None
        hasil = manual_teks.search_skor(topik, limit=1)
    except Exception:
        return None
    if not hasil or hasil[0][0] < _MANUAL_SKOR_MIN:
        return None
    _, r = hasil[0]
    return {"topik": topik, "sumber": r.get("sumber"), "halaman": r.get("halaman"),
            "judul": (r.get("judul") or "")[:100]}


def _kasus_serupa(teks: str, boleh_klaim: bool) -> dict | None:
    try:
        if not warranty_kasus.tersedia():
            return None
        r = warranty_kasus.cari(teks, batas_kasus=3, batas_part=6)
    except Exception:
        return None
    if not r or not r.get("found"):
        return None
    out = {
        "jumlah_kasus_mirip": r.get("jumlah_kasus_mirip"),
        "dari_total_kasus": r.get("dari_total_kasus"),
        "part_dipasang": [
            {"pn": p.get("pn"), "nama": p.get("nama"), "kali_dipasang": p.get("kali_dipasang"),
             "km_median": p.get("km_median"), "mode_gagal": p.get("mode_gagal_tersering")}
            for p in (r.get("part_disarankan") or [])[:6]
        ],
        "mode_gagal_tersering": (r.get("mode_gagal_tersering") or [])[:3],
    }
    if boleh_klaim:
        out["kasus_contoh"] = [
            {"no_wo": k.get("no_wo"), "km": k.get("km"), "gejala": (k.get("gejala") or "")[:120],
             "tindakan": (k.get("tindakan") or "")[:100]}
            for k in (r.get("kasus_contoh") or [])[:3]
        ]
    return out


def _label_keyakinan(sk: float, maks: float) -> str:
    if maks <= 0:
        return "rendah"
    r = sk / maks
    return "tinggi" if r >= 0.75 and sk >= 4 else ("sedang" if r >= 0.4 else "rendah")


def _hipotesis(sistem: str, teks: str, jawab: dict[str, str]) -> list[dict]:
    skor = _skor_sebab(sistem, teks, jawab)
    # Bukti klaim ikut menentukan urutan (pemecah seri & penambah keyakinan).
    kandidat = []
    for sk, d in skor:
        bukti = _bukti_klaim(d)
        n_klaim = sum(b.get("klaim", 0) for b in bukti)
        kandidat.append((sk + _BOBOT_BUKTI * math.log1p(n_klaim), sk, d, bukti, n_klaim))
    kandidat.sort(key=lambda x: (-x[0], x[2]["id"]))
    ada_sinyal = any(sk > 0 for _, sk, *_ in kandidat)
    if ada_sinyal:
        # Yang cocok keluhan dulu; sisanya (urut bukti) hanya mengisi sampai
        # _MIN_HIPOTESIS supaya selalu ada pembanding, ditandai cocok_keluhan=False.
        cocok = [k for k in kandidat if k[1] > 0]
        sisa = [k for k in kandidat if k[1] <= 0]
        kandidat = cocok + sisa[:max(0, _MIN_HIPOTESIS - len(cocok))]
    top = kandidat[:_BATAS_HIPOTESIS]
    maks = max((k[0] for k in top), default=0.0)
    out = []
    for i, (total, sk, d, bukti, n_klaim) in enumerate(top, 1):
        h = {
            "peringkat": i,
            "penyebab": d["nama"],
            "keyakinan": _label_keyakinan(total, maks) if ada_sinyal else "rendah",
            "cocok_keluhan": sk > 0,
            "cek": d["cek"],
            "klaim_garansi": n_klaim,
            "part_terkait": [
                {"nama": b["nama"], "pn": b["pn"][:2], "klaim": b["klaim"],
                 "km_median": b["km_median"],
                 "mode_rusak": [m["mode"] for m in b["mode_rusak"][:2]],
                 "diganti_bersama": [x["nama"] for x in b["diganti_bersama"][:3]]}
                for b in bukti
            ],
        }
        if i <= _HIPOTESIS_BERKODE:
            kode = _kode_untuk(d)
            if kode:
                h["kode_terkait"] = kode
            man = _manual_untuk(d)
            if man:
                h["manual"] = man
        h["topik_manual"] = d.get("manual")
        out.append(h)
    return out


# ── API utama ────────────────────────────────────────────────────────────────
def jalankan(keluhan: str, jawaban=None, sistem: str | None = None, *,
             username: str = "", boleh_klaim: bool = False,
             langsung: bool = False) -> dict:
    """Satu langkah wawancara. Mengembalikan kartu (`_tanya`) atau hasil.

    `jawaban`  : teks jawaban kartu giliran sebelumnya (atau dict/list).
    `langsung` : lewati wawancara, langsung peringkat dengan asumsi.
    """
    keluhan = (keluhan or "").strip()
    if not keluhan:
        return {"found": False, "catatan": "Sebutkan keluhan / gejalanya dulu."}
    teks_jawab = _teks_jawaban(jawaban)
    lewati = langsung or bool(_LEWATI_RE.search(teks_jawab))
    teks = _norm(keluhan)

    # 1. sistem — dari argumen, jawaban kartu pemilih, atau tebakan teks
    kode = None
    dari_pemilih = False
    if teks_jawab:
        kode = _pilih_sistem_dari_jawaban(teks_jawab)
        dari_pemilih = bool(kode)
    if not kode:
        kode, urut = _tebak_sistem(teks + " " + _norm(teks_jawab), sistem)
    else:
        urut = []
    if not kode:
        kunci = _kunci(username, keluhan, "pilih")
        if teks_jawab or lewati or _sudah_tanya(kunci):
            # sudah pernah ditanya / user melewati → ambil tebakan terkuat
            kode = urut[0][1] if urut else "mesin"
        else:
            _catat_tanya(kunci)
            opsi = [k for k, v in _PILIH_SISTEM_OPSI.items()
                    if any(v == u for _, u in urut[:4])] if urut else []
            if len(opsi) < 2:
                opsi = list(_PILIH_SISTEM_OPSI)[:4]
            return {
                "found": True, "tahap": "tanya", "sistem": None,
                "sistem_kandidat": [u for _, u in urut[:4]],
                "_tanya": [{"teks": _PILIH_SISTEM_TEKS, "opsi": opsi[:4]}],
                "_tanya_pengantar": f"Keluhan \"{keluhan}\" belum jelas sistemnya.",
                "_tanya_bebas_pagar": True,
                "catatan": "Kartu pemilih sistem ditampilkan; giliran berakhir. Setelah user "
                           "menjawab, panggil diagnosa_terpandu lagi dengan keluhan yang sama "
                           "dan jawaban = teks jawaban user.",
            }

    s = _SISTEM[kode]
    # 2. jawaban: otomatis dari teks keluhan + dari kartu
    jawab = _jawab_otomatis(kode, teks)
    jawab.update(_parse_jawaban(kode, teks_jawab))
    belum = [q for q in s["tanya"] if q["id"] not in jawab]

    kunci = _kunci(username, keluhan, kode)
    # Jawaban kartu PEMILIH sistem belum menjawab pertanyaan sistem itu → boleh
    # satu kartu lagi (pagar per-(user,keluhan,sistem) tetap mencegah ulangan).
    belum_dijawab = (not teks_jawab) or (dari_pemilih and not _parse_jawaban(kode, teks_jawab))
    if belum and not lewati and belum_dijawab and not _sudah_tanya(kunci):
        _catat_tanya(kunci)
        sementara = _skor_sebab(kode, teks, jawab)
        sementara.sort(key=lambda x: -x[0])
        return {
            "found": True, "tahap": "tanya", "sistem": kode, "sistem_label": s["label"],
            "jawaban_terdeteksi": jawab,
            "hipotesis_sementara": [d["nama"] for sk, d in sementara[:3]],
            "_tanya": [{"teks": q["teks"], "opsi": list(q["opsi"])} for q in belum[:3]],
            "_tanya_pengantar": f"Untuk mempersempit penyebab \"{keluhan}\" ({s['label'].lower()}):",
            "_tanya_bebas_pagar": True,
            "catatan": "Kartu pertanyaan ditampilkan; giliran berakhir. Setelah user menjawab, "
                       "panggil diagnosa_terpandu lagi dengan keluhan yang sama dan "
                       "jawaban = teks jawaban user apa adanya.",
        }

    # 3. peringkat + bukti
    teks_lengkap = teks + " " + _norm(" ".join(jawab.values()))
    hip = _hipotesis(kode, teks_lengkap, jawab)
    kasus = _kasus_serupa(keluhan + " " + " ".join(jawab.values()), boleh_klaim)
    asumsi = [q["teks"] for q in belum] if belum else []
    top = hip[0]["penyebab"] if hip else "-"
    out = {
        "found": True, "tahap": "hasil", "sistem": kode, "sistem_label": s["label"],
        "keluhan": keluhan,
        "jawaban_dipakai": jawab,
        "pertanyaan_tak_terjawab": asumsi,
        "hasil": hip,
    }
    if kasus:
        out["klaim_serupa"] = kasus
    out["ringkasan"] = (f"{s['label']}: {len(hip)} hipotesis, teratas '{top}'"
                        + (f"; {kasus['jumlah_kasus_mirip']} klaim serupa" if kasus else ""))
    out["catatan"] = (
        "Sajikan 'hasil' sebagai daftar penyebab BERPERINGKAT (maks 5): nama penyebab, "
        "keyakinan, lalu langkah 'cek' berurutan. 'part_terkait' = bukti dari klaim garansi "
        "armada (berapa klaim, km median, mode rusak, part yang biasanya diganti bersamaan) — "
        "sebut angkanya, jangan dilebih-lebihkan; 0 klaim = belum pernah diklaim, bukan berarti "
        "tak mungkin. 'kode_terkait' = kode kesalahan yang PATUT dicek di ECU, bukan kode yang "
        "sudah pasti aktif. 'pertanyaan_tak_terjawab' = asumsi; sebutkan singkat. "
        "⛔ Jangan menambah penyebab di luar 'hasil' dari pengetahuan umum. Bila user menyebut "
        "nomor rangka dan ingin PN/stok/harga, lanjutkan dengan cari_part_di_unit untuk part "
        "teratas; untuk prosedur rinci tawarkan cari_manual dengan 'topik_manual'. Tutup dengan "
        "1 pertanyaan tindak lanjut yang paling membedakan penyebab #1 dan #2."
    )
    return out
