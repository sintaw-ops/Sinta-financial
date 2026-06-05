import streamlit as st
import pandas as pd
from psycopg2.extras import execute_values
import re
from datetime import datetime, date
import plotly.express as px
import plotly.graph_objects as go

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

MONTHLY_BUDGET = 11_500_000
ALL_TYPES = ["Income", "Expense", "Transfer"]

CATEGORY_SEED = [
    ("Income",   "Salary",                    "salary",                  0),
    ("Income",   "Bonus",                     "bonus / thr",             0),
    ("Income",   "Investment Return",         "dividen",                 0),
    ("Income",   "Investment Return",         "bunga tabungan",          0),
    ("Income",   "Other Income",              "other income",            0),
    ("Expense",  "Essential Living",          "food",              2_500_000),
    ("Expense",  "Essential Living",          "transport",         1_000_000),
    ("Expense",  "Essential Living",          "utility",             500_000),
    ("Expense",  "Health & Wellness",         "sports",            1_500_000),
    ("Expense",  "Health & Wellness",         "nutritions",        1_000_000),
    ("Expense",  "Health & Wellness",         "medical care",        500_000),
    ("Expense",  "Family & Social",           "family",            2_000_000),
    ("Expense",  "Family & Social",           "donation",            500_000),
    ("Expense",  "Family & Social",           "team gathering",      300_000),
    ("Expense",  "Lifestyle & Personal Care", "shopping",          1_000_000),
    ("Expense",  "Lifestyle & Personal Care", "skincare and make up", 750_000),
    ("Expense",  "Education & Growth",        "education",           500_000),
    ("Expense",  "Wealth & Sinking Fund",     "invest gold",       2_000_000),
    ("Expense",  "Wealth & Sinking Fund",     "stock investment",  1_000_000),
    ("Expense",  "Wealth & Sinking Fund",     "emergency",         1_000_000),
    ("Expense",  "Wealth & Sinking Fund",     "gift",                300_000),
    ("Expense",  "Wealth & Sinking Fund",     "holiday fund",      2_000_000),
    ("Expense",  "Uncategorized",             "uncategorized",           0),
    ("Transfer", "Internal Transfer",         "internal transfer",       0),
]

CATEGORY_COLORS = {
    "Essential Living":          "#E24B4A",
    "Health & Wellness":         "#378ADD",
    "Family & Social":           "#639922",
    "Wealth & Sinking Fund":     "#BA7517",
    "Lifestyle & Personal Care": "#7F77DD",
    "Education & Growth":        "#D85A30",
    "Uncategorized":             "#888780",
}


def hex_to_rgba(hex_color: str, alpha: float = 0.15) -> str:
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE LAYER
# ══════════════════════════════════════════════════════════════════════════════

@st.cache_resource
def get_conn():
    import psycopg2
    db_uri = (
        f"postgresql://{st.secrets['DB_USER']}:{st.secrets['DB_PASSWORD']}"
        f"@{st.secrets['DB_HOST']}:{st.secrets['DB_PORT']}/{st.secrets['DB_NAME']}"
        f"?sslmode=require"
    )
    conn = psycopg2.connect(db_uri, connect_timeout=10)
    conn.autocommit = False
    return conn


def _conn():
    conn = get_conn()
    try:
        if conn.closed:
            raise Exception("closed")
        with conn.cursor() as c:
            c.execute("SELECT 1")
        return conn
    except Exception:
        get_conn.clear()
        return get_conn()


@st.cache_resource
def init_db():
    """Jalankan SEKALI — buat tabel + tambah kolom source jika belum ada."""
    conn = _conn()
    with conn.cursor() as c:
        c.execute("""
            CREATE TABLE IF NOT EXISTS categories (
                id SERIAL PRIMARY KEY,
                tx_type TEXT NOT NULL,
                parent_category TEXT NOT NULL,
                sub_category TEXT NOT NULL UNIQUE,
                monthly_budget REAL NOT NULL DEFAULT 0
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS transactions (
                id SERIAL PRIMARY KEY,
                date DATE NOT NULL,
                description TEXT NOT NULL,
                amount REAL NOT NULL,
                type TEXT NOT NULL,
                category TEXT NOT NULL,
                sub_category TEXT NOT NULL DEFAULT 'uncategorized',
                pocket TEXT,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                CONSTRAINT unique_tx UNIQUE(date, description, amount)
            )
        """)
        c.execute("""
            CREATE TABLE IF NOT EXISTS pocket_balance (
                id SERIAL PRIMARY KEY,
                pocket_name TEXT NOT NULL UNIQUE,
                balance REAL NOT NULL DEFAULT 0,
                pocket_type TEXT NOT NULL DEFAULT 'bank',
                is_cc BOOLEAN NOT NULL DEFAULT FALSE,
                updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        """)
        c.execute("""
            INSERT INTO pocket_balance (pocket_name, pocket_type, is_cc, balance)
            VALUES
                ('Bank Jago',  'bank', FALSE, 0),
                ('Jenius CC',  'cc',   TRUE,  0),
                ('Sinarmas',   'bank', FALSE, 0)
            ON CONFLICT (pocket_name) DO NOTHING
        """)
        # ── Migrasi: tambah kolom source jika belum ada ──────────────────────
        c.execute("""
            ALTER TABLE transactions
            ADD COLUMN IF NOT EXISTS source TEXT DEFAULT 'manual'
        """)
        c.execute("SELECT COUNT(*) FROM categories")
        if c.fetchone()[0] == 0:
            execute_values(
                c,
                """INSERT INTO categories (tx_type, parent_category, sub_category, monthly_budget)
                   VALUES %s ON CONFLICT DO NOTHING""",
                CATEGORY_SEED,
            )
    conn.commit()
    return True


@st.cache_data(ttl=600)
def load_categories_df():
    return pd.read_sql_query(
        "SELECT * FROM categories ORDER BY tx_type, parent_category, sub_category",
        _conn()
    )


@st.cache_data(ttl=600)
def get_sub_to_cat_map():
    df = load_categories_df()
    return dict(zip(df["sub_category"].str.lower(), df["parent_category"]))


def sub_to_cat(sub: str, tx_type: str) -> str:
    if tx_type == "Transfer":
        return "Internal Transfer"
    mapping = get_sub_to_cat_map()
    return mapping.get(sub.lower(), "Other Income" if tx_type == "Income" else "Uncategorized")


@st.cache_data(ttl=300)
def load_all_transactions():
    df = pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC", _conn())
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df


def save_transactions(df: pd.DataFrame) -> tuple[int, int]:
    """Batch insert — toleran terhadap kolom source yang ada/tidak ada."""
    conn = _conn()
    rows = []
    for _, row in df.iterrows():
        sub     = str(row.get("sub_category", "uncategorized")).strip()
        tx_type = str(row["type"])
        source  = str(row.get("source", "manual"))
        rows.append((
            str(row["date"])[:10],
            str(row["description"]),
            float(row["amount"]),
            tx_type,
            sub_to_cat(sub, tx_type),
            sub,
            str(row.get("pocket", "")),
            source,
        ))
    if not rows:
        return 0, 0
    with conn.cursor() as c:
        execute_values(
            c,
            """INSERT INTO transactions
               (date, description, amount, type, category, sub_category, pocket, source)
               VALUES %s ON CONFLICT (date, description, amount) DO NOTHING""",
            rows,
        )
        inserted = c.rowcount
    conn.commit()
    load_all_transactions.clear()
    return inserted, len(rows) - inserted


def bulk_update_categories(updates: list[tuple[str, str, int]]):
    """Update sub_category + category untuk banyak transaksi sekaligus."""
    conn = _conn()
    with conn.cursor() as c:
        for new_sub, new_cat, tid in updates:
            c.execute(
                "UPDATE transactions SET sub_category=%s, category=%s WHERE id=%s",
                (new_sub, new_cat, tid),
            )
    conn.commit()
    load_all_transactions.clear()


def update_sub_category(tid: int, new_sub: str, tx_type: str):
    conn = _conn()
    with conn.cursor() as c:
        c.execute(
            "UPDATE transactions SET sub_category=%s, category=%s WHERE id=%s",
            (new_sub, sub_to_cat(new_sub, tx_type), tid),
        )
    conn.commit()
    load_all_transactions.clear()


def delete_transaction(tid: int):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("DELETE FROM transactions WHERE id = %s", (tid,))
    conn.commit()
    load_all_transactions.clear()


def bulk_delete_transactions(ids: list[int]):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("DELETE FROM transactions WHERE id = ANY(%s)", (ids,))
    conn.commit()
    load_all_transactions.clear()


def upsert_category(tx_type: str, parent: str, sub: str, budget: float):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO categories (tx_type, parent_category, sub_category, monthly_budget)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (sub_category)
            DO UPDATE SET tx_type=EXCLUDED.tx_type,
                          parent_category=EXCLUDED.parent_category,
                          monthly_budget=EXCLUDED.monthly_budget
        """, (tx_type, parent, sub.lower().strip(), budget))
    conn.commit()
    load_categories_df.clear()
    get_sub_to_cat_map.clear()


def delete_category(sub: str):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("DELETE FROM categories WHERE sub_category = %s", (sub,))
    conn.commit()
    load_categories_df.clear()
    get_sub_to_cat_map.clear()


def update_budget(sub: str, new_budget: float):
    conn = _conn()
    with conn.cursor() as c:
        c.execute(
            "UPDATE categories SET monthly_budget = %s WHERE sub_category = %s",
            (new_budget, sub)
        )
    conn.commit()
    load_categories_df.clear()


# ── Pocket Balance Functions ──────────────────────────────────────────────────

@st.cache_data(ttl=60)
def load_pocket_balances() -> pd.DataFrame:
    return pd.read_sql_query(
        "SELECT * FROM pocket_balance ORDER BY is_cc, pocket_name",
        _conn()
    )


def update_pocket_balance(pocket_name: str, new_balance: float):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO pocket_balance (pocket_name, balance, updated_at)
            VALUES (%s, %s, NOW())
            ON CONFLICT (pocket_name)
            DO UPDATE SET balance = EXCLUDED.balance, updated_at = NOW()
        """, (pocket_name, new_balance))
    conn.commit()
    load_pocket_balances.clear()


def add_pocket(pocket_name: str, pocket_type: str, is_cc: bool, balance: float):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("""
            INSERT INTO pocket_balance (pocket_name, pocket_type, is_cc, balance)
            VALUES (%s, %s, %s, %s)
            ON CONFLICT (pocket_name) DO NOTHING
        """, (pocket_name, pocket_type, is_cc, balance))
    conn.commit()
    load_pocket_balances.clear()


def delete_pocket(pocket_name: str):
    conn = _conn()
    with conn.cursor() as c:
        c.execute("DELETE FROM pocket_balance WHERE pocket_name = %s", (pocket_name,))
    conn.commit()
    load_pocket_balances.clear()


def compute_net_balance(pocket_name: str, df_all: pd.DataFrame) -> dict:
    """
    Hitung net balance per pocket berdasarkan transaksi.
    Untuk CC: balance = hutang (pengeluaran CC - pembayaran tagihan CC)
    Untuk bank: balance = saldo (pemasukan - pengeluaran - transfer keluar + transfer masuk)
    """
    df_p = df_all[df_all["pocket"] == pocket_name].copy()
    if df_p.empty:
        return {"income": 0, "expense": 0, "transfer_in": 0, "transfer_out": 0, "net": 0}

    income       = df_p[df_p["type"] == "Income"]["amount"].sum()
    expense      = df_p[df_p["type"] == "Expense"]["amount"].sum()
    transfer_out = df_p[df_p["type"] == "Transfer"]["amount"].sum()

    # Transfer masuk = transaksi Transfer di pocket LAIN yang tujuannya ke pocket ini
    # (Untuk CC: pembayaran tagihan = Transfer masuk ke CC)
    cc_payments = df_all[
        (df_all["type"] == "Transfer") &
        (df_all["sub_category"] == "bayar tagihan cc") &
        (df_all["description"].str.contains("jenius cc", case=False, na=False)
         if pocket_name == "Jenius CC" else pd.Series([False]*len(df_all), index=df_all.index))
    ]["amount"].sum()

    net = income - expense - transfer_out + cc_payments
    return {
        "income": income, "expense": expense,
        "transfer_out": transfer_out, "cc_payments": cc_payments,
        "net": net,
    }


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PDF PARSER
# ══════════════════════════════════════════════════════════════════════════════

def extract_pdf_data(file, password, bank_type):
    import pdfplumber
    transactions = []
    try:
        with pdfplumber.open(file, password=password if password else None) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text:
                    continue
                for line in text.split('\n'):
                    if bank_type == "Sinarmas":
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+(.+?)\s+([\d\,\.]+)\s*$', line)
                        if match:
                            amt_str = match.group(3).replace(',', '')
                            if amt_str.count('.') > 1:
                                amt_str = amt_str.rsplit('.', 1)[0]
                            amount = float(amt_str)
                            tx_type = "Income" if "Incoming" in line or "Credit" in line else "Expense"
                            transactions.append({
                                "date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"),
                                "description": match.group(2)[:40], "amount": amount,
                                "type": tx_type, "pocket": "Sinarmas",
                                "sub_category": "uncategorized", "category": "Uncategorized",
                                "source": "pdf",
                            })
                    elif bank_type == "Jenius CC":
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+\d{2}\s[a-zA-Z]{3}\s\d{4}\s+(.+?)\s+([\d\,\.]+)(?:\sCR)?$', line)
                        if match:
                            amount = float(match.group(3).replace(',', ''))
                            tx_type = "Income" if "CR" in line or "Pembayaran" in line else "Expense"
                            transactions.append({
                                "date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"),
                                "description": match.group(2)[:40], "amount": amount,
                                "type": tx_type, "pocket": "Jenius CC",
                                "sub_category": "uncategorized", "category": "Uncategorized",
                                "source": "pdf",
                            })
                    elif bank_type == "Bank Jago":
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+\d{2}\.\d{2}\s+(.+?)\s+([\-\+])([\d\.]+)', line)
                        if match:
                            amount = float(match.group(4).replace('.', ''))
                            tx_type = "Income" if match.group(3) == "+" else "Expense"
                            transactions.append({
                                "date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"),
                                "description": match.group(2)[:40], "amount": amount,
                                "type": tx_type, "pocket": "Bank Jago",
                                "sub_category": "uncategorized", "category": "Uncategorized",
                                "source": "pdf",
                            })
    except Exception as e:
        return None, str(e)
    return pd.DataFrame(transactions), None


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — EMAIL PARSER (IMAP)
# ══════════════════════════════════════════════════════════════════════════════

def _parse_email_date(date_str: str) -> str:
    for fmt in ["%a, %d %b %Y %H:%M:%S %z", "%d %b %Y %H:%M:%S %z",
                "%a, %d %b %Y %H:%M:%S %Z", "%d %b %Y %H:%M:%S"]:
        try:
            return datetime.strptime(date_str[:31].strip(), fmt).strftime("%Y-%m-%d")
        except ValueError:
            continue
    return datetime.now().strftime("%Y-%m-%d")


def _extract_amount_jenius_cc(body: str) -> float | None:
    """
    Parser khusus Jenius CC.
    Format email: 'Total: IDR 154,000.00'
    Harus ambil angka SETELAH kata 'Total:' saja, bukan dari bagian lain email
    (card number, dll yang bisa menghasilkan angka salah).
    """
    # Pola spesifik: Total: IDR xxx,xxx.xx
    m = re.search(r"Total[:\s]+IDR\s*([\d,]+(?:\.\d{1,2})?)", body, re.IGNORECASE)
    if m:
        raw = m.group(1).replace(",", "")  # "154,000.00" → "154000.00"
        try:
            return float(raw)
        except ValueError:
            pass
    # Fallback: cari "IDR xxx,xxx" pola umum tapi pastikan bukan card number
    # Card number punya format: 437896******2961 (ada bintang), skip
    body_clean = re.sub(r'\d{6}\*+\d{4}', '', body)  # hapus card number
    m2 = re.search(r"IDR\s*([\d,]+(?:\.\d{1,2})?)", body_clean, re.IGNORECASE)
    if m2:
        raw = m2.group(1).replace(",", "")
        try:
            val = float(raw)
            if val > 100:
                return val
        except ValueError:
            pass
    return None


def _extract_amount_general(body: str) -> float | None:
    """Parser amount untuk Bank Jago dan Sinarmas."""
    # Hapus card number dulu (pola: digit-bintang-digit)
    body_clean = re.sub(r'\d{4,6}\*+\d{4}', '', body)
    for pat in [
        r"(?:sebesar|jumlah|nominal|amount)[:\s]+Rp\.?\s*([\d\.]+)",
        r"Rp\.?\s*([\d\.]+(?:,\d{2})?)",
        r"IDR\s*([\d,]+(?:\.\d{1,2})?)",
    ]:
        m = re.search(pat, body_clean, re.IGNORECASE)
        if m:
            raw = m.group(1).replace(".", "").replace(",", "")
            try:
                val = float(raw)
                if val > 100:
                    return val
            except ValueError:
                continue
    return None


def _detect_tx_type_jenius_cc(subject: str, body: str) -> str:
    """
    Klasifikasi email Jenius CC:

    TRANSFER  : bayar tagihan CC (perpindahan internal, bukan pengeluaran baru)
                Subject 'You Just Paid Your Jenius Credit Card Bills'
                -> dicatat Transfer agar tidak double-count dg transaksi CC asli

    INCOME    : refund / cashback / reversal ke CC

    EXPENSE   : semua transaksi belanja di merchant
    """
    subj = subject.lower()
    text = (subject + " " + body).lower()

    # TRANSFER: bayar tagihan CC
    bill_payment_kw = [
        "you just paid your jenius credit card",
        "paid your jenius credit card bills",
        "credit card bill has been received",
        "pembayaran tagihan kartu kredit",
    ]
    if any(k in text for k in bill_payment_kw):
        return "Transfer"

    # INCOME: refund / cashback / reversal
    income_kw = ["refund", "cashback", "reversal", "pembayaran tagihan diterima"]
    if any(k in text for k in income_kw):
        return "Income"

    # EXPENSE: transaksi belanja di merchant
    return "Expense"


def _detect_tx_type_general(subject: str, body: str, pocket: str) -> str:
    """Deteksi tipe transaksi untuk Bank Jago dan Sinarmas."""
    text = (subject + " " + body).lower()
    subj = subject.lower()

    # Bank Jago: deteksi dari subject yang sangat spesifik
    if "melakukan transfer" in subj or "kamu telah melakukan" in subj:
        return "Expense"
    if "menerima transfer" in subj or "dana masuk" in subj or "uang masuk" in subj:
        return "Income"
    if "pembayaran berhasil" in subj or "tagihan berhasil" in subj:
        return "Expense"
    if "top up berhasil" in subj:
        return "Income"

    income_kw = [
        "transfer masuk", "incoming transfer", "menerima transfer",
        "dana masuk", "kredit masuk", "credit received",
        "refund", "cashback", "bunga tabungan", "dividen",
    ]
    expense_kw = [
        "transfer keluar", "melakukan transfer", "debit", "pembayaran",
        "payment", "belanja", "withdraw", "tarik tunai", "purchase",
        "transaksi", "qris", "transfer out", "tagihan", "cicilan",
    ]
    if any(k in text for k in income_kw):
        return "Income"
    if any(k in text for k in expense_kw):
        return "Expense"
    return "Expense"


def _extract_amount_jago(body: str) -> float | None:
    """
    Parser amount khusus Bank Jago.
    Format: 'Jumlah      Rp30.000' (titik = pemisah ribuan, BUKAN desimal)
    """
    # Cari pola spesifik Jago: 'Jumlah' diikuti nominal
    m = re.search(r"Jumlah\s+Rp([\d\.]+)", body)
    if m:
        raw = m.group(1).replace(".", "")  # Rp30.000 → 30000
        try:
            val = float(raw)
            if val > 100:
                return val
        except ValueError:
            pass
    # Fallback: Rp dengan format titik ribuan
    m2 = re.search(r"Rp\s*([\d\.]+)", body)
    if m2:
        raw = m2.group(1).replace(".", "")
        try:
            val = float(raw)
            if val > 100:
                return val
        except ValueError:
            pass
    return None


def _extract_description_jago(body: str, subject: str) -> str:
    """
    Ekstrak deskripsi dari email Bank Jago.
    Format: field 'Ke' berisi nama penerima + baris berikutnya berisi nama bank.
    Hasil: 'Transfer ke AHMAD MUSTAQFIRIN - Bank Sinarmas'
    """
    lines = [l.strip() for l in body.splitlines() if l.strip()]
    subj  = subject.lower()

    if "melakukan transfer" in subj:
        # Cari nama penerima dari field 'Ke'
        for i, line in enumerate(lines):
            if re.match(r"^Ke\s+", line, re.IGNORECASE):
                nama = re.sub(r"^Ke\s+", "", line, flags=re.IGNORECASE).strip()
                # Baris berikutnya kemungkinan nama bank
                bank_info = ""
                if i + 1 < len(lines):
                    nxt = lines[i + 1]
                    if re.search(r"Bank\s+\w+", nxt, re.IGNORECASE):
                        bank_part = re.search(r"(Bank\s+\w+)", nxt, re.IGNORECASE)
                        if bank_part:
                            bank_info = f" - {bank_part.group(1)}"
                if nama:
                    return f"Transfer ke {nama}{bank_info}"[:50]

    if "menerima transfer" in subj or "dana masuk" in subj:
        # Cari pengirim dari field 'Dari'
        for line in lines:
            if re.match(r"^Dari\s+", line, re.IGNORECASE):
                pengirim = re.sub(r"^Dari\s+", "", line, flags=re.IGNORECASE).strip()
                # Hapus kode rekening (DC • 1066...)
                pengirim = re.sub(r"DC\s*[•\-]\s*\d+", "", pengirim).strip()
                if pengirim:
                    return f"Transfer dari {pengirim}"[:50]

    return subject[:40] if subject else "Bank Jago Transaction"


def _extract_date_jago(body: str) -> str | None:
    """Parse tanggal dari email Bank Jago. Format: '05 June 2026 07:34 WIB'"""
    m = re.search(r"Tanggal transaksi\s+(\d{1,2}\s+\w+\s+\d{4})", body, re.IGNORECASE)
    if m:
        try:
            return datetime.strptime(m.group(1), "%d %B %Y").strftime("%Y-%m-%d")
        except ValueError:
            pass
    return None


def parse_bank_email(subject: str, body: str, email_date: str, sender: str) -> dict | None:
    try:
        tx_date = _parse_email_date(email_date)

        is_jenius_cc = "jenius" in sender or "btpn" in sender or "smbci" in sender
        is_jago      = "jago" in sender
        is_sinarmas  = "sinarmas" in sender

        if is_jenius_cc:
            pocket  = "Jenius CC"
            tx_type = _detect_tx_type_jenius_cc(subject, body)

            if tx_type == "Transfer":
                # ── Bayar tagihan CC ─────────────────────────────────────
                # Amount: 'Payment in the amount of IDR3,000,000'
                m_amt = re.search(r"amount of IDR\s*([\d,]+)", body, re.IGNORECASE)
                if m_amt:
                    try:
                        amount = float(m_amt.group(1).replace(",", ""))
                    except ValueError:
                        amount = _extract_amount_jenius_cc(body)
                else:
                    amount = _extract_amount_jenius_cc(body)
                description = "Bayar tagihan Jenius CC"
                # Tanggal dari email header (tidak ada di body email ini)
                # tx_date sudah di-set dari _parse_email_date di atas

            else:
                # ── Transaksi belanja di merchant ─────────────────────────
                amount      = _extract_amount_jenius_cc(body)
                description = subject[:40]
                # Merchant dari body: 'Merchant: YOSHINOYA BDR SEPINGGAN R'
                m = re.search(r"Merchant[:\s]+([^\r\n]{3,40})", body, re.IGNORECASE)
                if m:
                    desc_c = m.group(1).strip()
                    if len(desc_c) > 3 and not re.match(r"^\d+$", desc_c):
                        description = desc_c[:40]
                # Tanggal dari body: 'Transaction date & time: 01/06/2026 16:09:44'
                m_d = re.search(r"Transaction date[^:]*[:\s]+(\d{2}/\d{2}/\d{4})", body, re.IGNORECASE)
                if m_d:
                    try:
                        tx_date = datetime.strptime(m_d.group(1), "%d/%m/%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        pass

        elif is_jago:
            pocket      = "Bank Jago"
            amount      = _extract_amount_jago(body)
            tx_type     = _detect_tx_type_general(subject, body, pocket)
            description = _extract_description_jago(body, subject)
            date_jago   = _extract_date_jago(body)
            if date_jago:
                tx_date = date_jago

        elif is_sinarmas:
            pocket = "Sinarmas"

            # ── Deteksi format email Sinarmas ─────────────────────────────
            # Format A: noreply.care@banksinarmas.com (Email Notifikasi)
            #   Field: Tanggal, Nilai Transaksi, Jenis Transaksi, Nomor Referensi
            #   Jenis Transaksi: "BI Fast Payment Cr" (Cr=Income) / "Db" (Expense)
            # Format B: transaction@banksinarmas.com (Simobi+ Transfer)
            #   Field: Transaction ID, Transaction date, From, Paid to, Amount

            is_format_b = (
                "transaction@banksinarmas" in sender or
                "transfer successful" in body.lower() or
                "paid to" in body.lower()
            )

            if is_format_b:
                # ── Format B: Simobi+ Transfer Successful ─────────────────
                # Selalu Expense (transfer keluar dari rekening Sinta)
                tx_type = "Expense"

                # Amount: 'Amount    Rp450.000' atau 'Total payment    Rp450.000'
                # Ambil 'Amount' bukan 'Admin fee' yang selalu kecil
                m_amt = re.search(r"(?:^|\n)\s*Amount\s+Rp([\d\.]+)", body, re.IGNORECASE | re.MULTILINE)
                if not m_amt:
                    m_amt = re.search(r"Total payment\s+Rp([\d\.]+)", body, re.IGNORECASE)
                amount = None
                if m_amt:
                    raw = m_amt.group(1).replace(".", "")
                    try:
                        amount = float(raw)
                    except ValueError:
                        pass
                if not amount:
                    amount = _extract_amount_general(body)

                # Deskripsi: ambil nama penerima dari field 'Paid to'
                m_to = re.search(r"Paid to\s+([A-Z][^\n\r]+)", body, re.IGNORECASE)
                if m_to:
                    penerima = m_to.group(1).strip()
                    # Bersihkan nama bank di baris berikutnya jika ada
                    penerima = re.split(r"\s*-\s*BANK\s", penerima)[0].strip()
                    description = f"Transfer ke {penerima}"[:50]
                else:
                    description = subject[:40]

                # Tanggal: 'Transaction date    04 Jun 2026 16:11:26 WIB'
                m_d = re.search(
                    r"Transaction date\s+(\d{2}\s+\w+\s+\d{4})",
                    body, re.IGNORECASE
                )
                if m_d:
                    for fmt in ["%d %b %Y", "%d %B %Y"]:
                        try:
                            tx_date = datetime.strptime(m_d.group(1), fmt).strftime("%Y-%m-%d")
                            break
                        except ValueError:
                            continue

            else:
                # ── Format A: Email Notifikasi (noreply.care) ─────────────
                # Deteksi Income/Expense dari field 'Jenis Transaksi'
                # Cr  = Credit  = Income (uang masuk)
                # Db  = Debit   = Expense (uang keluar)
                jenis_m = re.search(r"Jenis Transaksi\s+[:\-]?\s*(.+)", body, re.IGNORECASE)
                if jenis_m:
                    jenis = jenis_m.group(1).strip().lower()
                    if jenis.endswith(" cr") or " cr " in jenis or jenis == "cr":
                        tx_type = "Income"
                    elif jenis.endswith(" db") or " db " in jenis or jenis == "db":
                        tx_type = "Expense"
                    else:
                        tx_type = _detect_tx_type_general(subject, body, pocket)
                else:
                    tx_type = _detect_tx_type_general(subject, body, pocket)

                # Amount: 'Nilai Transaksi    : IDR 11,720,000.00'
                m_amt = re.search(r"Nilai Transaksi\s*[:\-]?\s*IDR\s*([\d,]+(?:\.\d{1,2})?)", body, re.IGNORECASE)
                amount = None
                if m_amt:
                    raw = m_amt.group(1).replace(",", "")
                    try:
                        amount = float(raw)
                    except ValueError:
                        pass
                if not amount:
                    amount = _extract_amount_general(body)

                # Deskripsi: ambil Jenis Transaksi sebagai deskripsi
                if jenis_m:
                    jenis_full = jenis_m.group(1).strip()
                    description = f"Sinarmas - {jenis_full}"[:50]
                else:
                    description = subject[:40]

                # Tanggal: 'Tanggal    : 25-05-2026'
                m_d = re.search(r"Tanggal\s*[:\-]?\s*(\d{2}[-/]\d{2}[-/]\d{4})", body, re.IGNORECASE)
                if m_d:
                    raw_d = m_d.group(1).replace("-", "/")
                    try:
                        tx_date = datetime.strptime(raw_d, "%d/%m/%Y").strftime("%Y-%m-%d")
                    except ValueError:
                        pass
        else:
            pocket      = "Email"
            amount      = _extract_amount_general(body)
            tx_type     = _detect_tx_type_general(subject, body, pocket)
            description = subject[:40]

        if not amount:
            return None

        return {
            "date": tx_date, "description": description, "amount": amount,
            "type": tx_type, "pocket": pocket,
            "sub_category": "uncategorized", "category": "Uncategorized",
            "source": "email",
        }
    except Exception:
        return None



def fetch_email_transactions(email_addr: str, app_password: str,
                              days_back: int = 7) -> tuple[list, list, str]:
    import imaplib
    import email as email_lib
    from email.header import decode_header

    transactions, raw_emails, errors = [], [], []

    try:
        mail = imaplib.IMAP4_SSL("imap.gmail.com", 993)
        mail.login(email_addr, app_password)
    except Exception as e:
        return [], [], f"Gagal login Gmail: {e}"

    try:
        mail.select("INBOX")
        since = (datetime.now() - __import__('datetime').timedelta(days=days_back)).strftime("%d-%b-%Y")

        for keyword in ["jago", "btpn", "jenius", "sinarmas"]:
            try:
                _, ids = mail.search(None, f'(SINCE "{since}" FROM "{keyword}")')
                for msg_id in ids[0].split():
                    try:
                        _, data = mail.fetch(msg_id, "(RFC822)")
                        msg = email_lib.message_from_bytes(data[0][1])
                        raw_s, enc = decode_header(msg["Subject"] or "")[0]
                        subject  = raw_s.decode(enc or "utf-8", errors="ignore") if isinstance(raw_s, bytes) else (raw_s or "")
                        sender   = msg.get("From", "").lower()
                        date_str = msg.get("Date", "")
                        body     = ""
                        if msg.is_multipart():
                            for part in msg.walk():
                                if part.get_content_type() == "text/plain":
                                    try:
                                        body = part.get_payload(decode=True).decode("utf-8", errors="ignore")
                                        break
                                    except Exception:
                                        pass
                        else:
                            try:
                                body = msg.get_payload(decode=True).decode("utf-8", errors="ignore")
                            except Exception:
                                pass
                        raw_emails.append({"subject": subject, "from": sender,
                                           "date": date_str, "body": body[:500]})
                        tx = parse_bank_email(subject, body, date_str, sender)
                        if tx:
                            transactions.append(tx)
                    except Exception as e:
                        errors.append(f"msg {msg_id}: {e}")
            except Exception as e:
                errors.append(f"search [{keyword}]: {e}")
        mail.logout()
    except Exception as e:
        errors.append(f"IMAP: {e}")

    return transactions, raw_emails, "; ".join(errors)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def fmt_idr(v: float) -> str:
    return f"Rp {v:,.0f}".replace(",", ".")


def plotly_base() -> dict:
    return dict(
        plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=30, b=0), hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — UI: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def tab_dashboard(df_all: pd.DataFrame):
    st.title("📊 Financial Dashboard")
    if df_all.empty:
        st.info("Belum ada data.")
        return

    df_clean = df_all[
        (df_all["type"] != "Transfer") & (df_all["sub_category"] != "uncategorized")
    ].copy()
    df_clean["year"]    = df_clean["date"].dt.year
    df_clean["month"]   = df_clean["date"].dt.month
    df_clean["quarter"] = df_clean["date"].dt.to_period("Q").astype(str)

    with st.container(border=True):
        col1, col2, col3 = st.columns(3)
        sel_year  = col1.selectbox("Filter Tahun", sorted(df_clean["year"].unique(), reverse=True))
        view_mode = col2.radio("Mode Tampilan", ["Bulanan", "Kuartalan"], horizontal=True)
        df_year   = df_clean[df_clean["year"] == sel_year]

        if view_mode == "Bulanan":
            avail_months = sorted(df_year["month"].unique())
            sel_months   = col3.multiselect("Bulan", avail_months, default=avail_months,
                                             format_func=lambda m: datetime(2000, m, 1).strftime("%B"))
            df_filtered = df_year[df_year["month"].isin(sel_months)]
            time_group  = "date"
        else:
            avail_q     = sorted(df_year["quarter"].unique())
            sel_q       = col3.multiselect("Kuartal", avail_q, default=avail_q)
            df_filtered = df_year[df_year["quarter"].isin(sel_q)].copy()
            df_filtered["year_month"] = df_filtered["date"].dt.to_period("M").astype(str)
            time_group  = "year_month"

    if df_filtered.empty:
        st.warning("Tidak ada data untuk filter yang dipilih.")
        return

    inc_df = df_filtered[df_filtered["type"] == "Income"]
    exp_df = df_filtered[df_filtered["type"] == "Expense"]
    ti, te = inc_df["amount"].sum(), exp_df["amount"].sum()

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Pemasukan",   fmt_idr(ti))
    c2.metric("💸 Pengeluaran", fmt_idr(te))
    c3.metric("🏦 Net Income",  fmt_idr(ti - te))
    c4.metric("🔥 Burn Rate",   f"{(te / ti * 100) if ti > 0 else 0:.1f}%")
    st.divider()

    st.markdown("### 📉 Tren Pengeluaran vs Pemasukan")
    fig_line = go.Figure()
    for grp, color, label in [(exp_df, "#e74c3c", "Pengeluaran"), (inc_df, "#2ecc71", "Pemasukan")]:
        d = grp.groupby(time_group)["amount"].sum().reset_index()
        fig_line.add_trace(go.Scatter(x=d[time_group], y=d["amount"],
                                       mode="lines+markers", name=label,
                                       line=dict(color=color, width=3)))
    n_months = len(sel_months) if view_mode == "Bulanan" else 1
    fig_line.add_hline(y=MONTHLY_BUDGET * n_months, line_dash="dash",
                        line_color="#f39c12", line_width=2,
                        annotation_text=f"Budget ({fmt_idr(MONTHLY_BUDGET * n_months)})",
                        annotation_position="top right")
    fig_line.update_layout(**plotly_base())
    st.plotly_chart(fig_line, use_container_width=True)

    col_c1, col_c2 = st.columns(2)
    with col_c1:
        st.markdown("#### 💵 Sumber Pemasukan")
        inc_bar = inc_df.groupby("sub_category")["amount"].sum().reset_index().sort_values("amount", ascending=True)
        fig_inc = px.bar(inc_bar, x="amount", y="sub_category", orientation="h",
                         color_discrete_sequence=["#3498db"])
        fig_inc.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                               xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
        st.plotly_chart(fig_inc, use_container_width=True)

    with col_c2:
        st.markdown("#### 🏆 Alokasi Savings & Investasi")
        sav_df  = df_filtered[df_filtered["category"] == "Wealth & Sinking Fund"]
        sav_bar = sav_df.groupby("sub_category")["amount"].sum().reset_index().sort_values("amount", ascending=True)
        if not sav_bar.empty:
            fig_sav = px.bar(sav_bar, x="amount", y="sub_category", orientation="h",
                             color_discrete_sequence=["#f1c40f"])
            fig_sav.update_layout(plot_bgcolor="rgba(0,0,0,0)", paper_bgcolor="rgba(0,0,0,0)",
                                   xaxis_title="", yaxis_title="", margin=dict(l=0, r=0, t=10, b=0))
            st.plotly_chart(fig_sav, use_container_width=True)
        else:
            st.info("Belum ada alokasi saving di periode ini.")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — UI: BUDGET VS ACTUAL
# ══════════════════════════════════════════════════════════════════════════════

def tab_budget_vs_actual(df_all: pd.DataFrame):
    st.title("🎯 Budget vs Actual")
    if df_all.empty:
        st.info("Belum ada data transaksi.")
        return

    df_exp = df_all[(df_all["type"] == "Expense") & (df_all["sub_category"] != "uncategorized")].copy()
    df_exp["year"]  = df_exp["date"].dt.year
    df_exp["month"] = df_exp["date"].dt.month
    df_exp["day"]   = df_exp["date"].dt.day

    with st.container(border=True):
        col1, col2 = st.columns([1, 3])
        avail_years  = sorted(df_exp["year"].unique(), reverse=True) if not df_exp.empty else [datetime.now().year]
        sel_year     = col1.selectbox("Filter Tahun", avail_years)
        df_year      = df_exp[df_exp["year"] == sel_year]
        avail_months = sorted(df_year["month"].unique())
        sel_months   = col2.multiselect("Filter Bulan", avail_months, default=avail_months,
                                         format_func=lambda m: datetime(2000, m, 1).strftime("%B"))

    if not sel_months:
        st.warning("Pilih minimal satu bulan.")
        return

    df_f     = df_year[df_year["month"].isin(sel_months)]
    n_months = len(sel_months)
    cat_df   = load_categories_df()
    bud_map  = dict(zip(cat_df["sub_category"], cat_df["monthly_budget"]))
    total_budget = cat_df[cat_df["tx_type"] == "Expense"]["monthly_budget"].sum() * n_months
    total_actual = df_f["amount"].sum()
    burn_rate    = (total_actual / total_budget * 100) if total_budget > 0 else 0
    selisih      = total_budget - total_actual

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🎯 Total Budget",  fmt_idr(total_budget), f"{n_months} bulan")
    k2.metric("💸 Total Actual",  fmt_idr(total_actual))
    k3.metric("📊 Selisih", fmt_idr(abs(selisih)),
              delta=f"{'Under' if selisih >= 0 else 'Over'} budget",
              delta_color="normal" if selisih >= 0 else "inverse")
    k4.metric("🔥 Burn Rate", f"{burn_rate:.1f}%")
    st.divider()

    st.markdown("### 📊 Actual vs Budget per Sub-Kategori")
    actual_by_sub = df_f.groupby("sub_category")["amount"].sum().reset_index()
    actual_by_sub.columns = ["sub_category", "actual"]
    actual_by_sub["budget"] = actual_by_sub["sub_category"].map(bud_map).fillna(0) * n_months
    actual_by_sub["status"] = actual_by_sub.apply(
        lambda r: "Over Budget 🔴" if r["actual"] > r["budget"] else "Under Budget 🟢", axis=1)
    actual_by_sub = actual_by_sub.sort_values("actual", ascending=True)

    fig_bva = go.Figure()
    fig_bva.add_trace(go.Bar(
        y=actual_by_sub["sub_category"], x=actual_by_sub["actual"], name="Actual",
        orientation="h",
        marker_color=["#E24B4A" if r["actual"] > r["budget"] else "#639922"
                      for _, r in actual_by_sub.iterrows()],
    ))
    fig_bva.add_trace(go.Scatter(
        y=actual_by_sub["sub_category"], x=actual_by_sub["budget"], name="Budget",
        mode="markers", marker=dict(symbol="line-ns", size=14, color="#f39c12",
                                    line=dict(width=2, color="#f39c12")),
    ))
    fig_bva.update_layout(**plotly_base(), height=max(350, len(actual_by_sub) * 32),
                           xaxis_title="IDR", yaxis_title="")
    st.plotly_chart(fig_bva, use_container_width=True)

    st.markdown("### 📁 Realisasi per Kategori Induk")
    parent_budget = (cat_df[cat_df["tx_type"] == "Expense"]
                     .groupby("parent_category")["monthly_budget"].sum() * n_months).reset_index()
    parent_budget.columns = ["category", "budget"]
    actual_by_cat = df_f.groupby("category")["amount"].sum().reset_index()
    actual_by_cat.columns = ["category", "actual"]
    merged = parent_budget.merge(actual_by_cat, on="category", how="left").fillna(0)
    merged = merged[merged["budget"] > 0].sort_values("budget", ascending=False)

    for _, row in merged.iterrows():
        pct  = (row["actual"] / row["budget"] * 100) if row["budget"] > 0 else 0
        over = row["actual"] > row["budget"]
        lbl  = "🔴" if over else ("🟡" if pct > 85 else "🟢")
        c_a, c_b = st.columns([3, 1])
        c_a.markdown(f"**{row['category']}** {lbl}")
        c_b.markdown(f"`{fmt_idr(row['actual'])} / {fmt_idr(row['budget'])}` — **{min(pct,100):.1f}%**")
        st.progress(min(pct / 100, 1.0))
    st.divider()

    st.markdown("### 📅 Pengeluaran Harian — Total")
    import calendar
    total_days        = sum(calendar.monthrange(sel_year, m)[1] for m in sel_months)
    daily_budget_line = total_budget / total_days if total_days > 0 else MONTHLY_BUDGET / 30
    all_days          = pd.DataFrame({"day": range(1, 32)})
    daily_total       = df_f.groupby("day")["amount"].sum().reset_index()
    daily_total       = all_days.merge(daily_total, on="day", how="left").fillna(0)

    fig_daily = go.Figure()
    fig_daily.add_trace(go.Scatter(
        x=daily_total["day"], y=daily_total["amount"], mode="lines+markers",
        name="Actual Harian", line=dict(color="#E24B4A", width=2), marker=dict(size=5),
        fill="tozeroy", fillcolor=hex_to_rgba("#E24B4A", 0.08),
    ))
    fig_daily.add_hline(y=daily_budget_line, line_dash="dash", line_color="#f39c12",
                         line_width=2, annotation_text=f"Budget/hari ({fmt_idr(daily_budget_line)})",
                         annotation_position="top right")
    fig_daily.update_layout(**plotly_base(), height=320,
                             xaxis=dict(title="Tanggal", tickmode="linear", tick0=1, dtick=5),
                             yaxis=dict(title="IDR"))
    st.plotly_chart(fig_daily, use_container_width=True)

    st.markdown("### 🗂️ Pengeluaran Harian per Kategori")
    parent_cats = sorted(df_f["category"].unique().tolist())
    sel_cats    = st.multiselect("Pilih Kategori:", parent_cats, default=parent_cats)
    if sel_cats:
        df_cat_day = (df_f[df_f["category"].isin(sel_cats)]
                      .groupby(["day", "category"])["amount"].sum().reset_index())
        fig_cat = go.Figure()
        for cat in sel_cats:
            cat_data   = all_days.merge(df_cat_day[df_cat_day["category"] == cat], on="day", how="left").fillna(0)
            color      = CATEGORY_COLORS.get(cat, "#888780")
            fig_cat.add_trace(go.Scatter(
                x=cat_data["day"], y=cat_data["amount"], name=cat, mode="lines",
                line=dict(color=color, width=2), fill="tonexty",
                fillcolor=hex_to_rgba(color, 0.15), stackgroup="one",
            ))
        fig_cat.add_hline(y=daily_budget_line, line_dash="dash", line_color="#f39c12",
                           line_width=2, annotation_text=f"Budget/hari ({fmt_idr(daily_budget_line)})",
                           annotation_position="top right")
        fig_cat.update_layout(**plotly_base(), height=380,
                               xaxis=dict(title="Tanggal", tickmode="linear", tick0=1, dtick=5),
                               yaxis=dict(title="IDR"))
        st.plotly_chart(fig_cat, use_container_width=True)

    st.divider()
    st.markdown("### 📋 Tabel Ringkasan Aktual vs Budget")
    summary = actual_by_sub.copy()
    summary["selisih"] = summary["budget"] - summary["actual"]
    summary["pct"]     = (summary["actual"] / summary["budget"] * 100).where(summary["budget"] > 0, 0)
    summary = summary.sort_values("actual", ascending=False)
    display = summary[["sub_category", "budget", "actual", "selisih", "pct", "status"]].copy()
    display.columns = ["Sub-Kategori", "Budget (IDR)", "Actual (IDR)", "Selisih (IDR)", "% Realisasi", "Status"]
    for col in ["Budget (IDR)", "Actual (IDR)", "Selisih (IDR)"]:
        display[col] = display[col].apply(fmt_idr)
    display["% Realisasi"] = display["% Realisasi"].apply(lambda v: f"{v:.1f}%")
    st.dataframe(display, use_container_width=True, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — UI: VALIDASI ANTREAN (REDESIGNED)
# ══════════════════════════════════════════════════════════════════════════════

def tab_validation(df_all: pd.DataFrame):
    st.title("⚙️ Validasi & Kategorisasi Transaksi")

    uncat_df = df_all[df_all["sub_category"] == "uncategorized"].copy()
    cat_df   = load_categories_df()

    # ── KPI Bar ───────────────────────────────────────────────────────────────
    total_tx   = len(df_all)
    uncat_cnt  = len(uncat_df)
    done_cnt   = total_tx - uncat_cnt
    pct_done   = (done_cnt / total_tx * 100) if total_tx > 0 else 0

    k1, k2, k3, k4 = st.columns(4)
    k1.metric("📋 Total Transaksi", total_tx)
    k2.metric("✅ Sudah Dikategorikan", done_cnt)
    k3.metric("⏳ Menunggu Validasi", uncat_cnt,
              delta=f"{uncat_cnt} perlu diproses" if uncat_cnt > 0 else "Semua bersih",
              delta_color="inverse" if uncat_cnt > 0 else "normal")
    k4.metric("📊 Progress", f"{pct_done:.0f}%")
    st.progress(pct_done / 100)
    st.divider()

    if uncat_df.empty:
        st.success("🎉 Seluruh data transaksi sudah bersih dan terkategorisasi!")
    else:
        st.warning(f"⚠️ **{uncat_cnt}** transaksi belum dikategorikan.")

        # ── Tab mode validasi ─────────────────────────────────────────────────
        mode_tab1, mode_tab2 = st.tabs(["⚡ Validasi Massal (Bulk)", "🔍 Validasi Satu per Satu"])

        # ════════════════════════════════════════════════════════════════════
        # MODE 1: BULK VALIDATION
        # ════════════════════════════════════════════════════════════════════
        with mode_tab1:
            st.markdown("Assign kategori ke banyak transaksi sekaligus, lalu simpan semua dalam satu klik.")

            # Filter pocket
            pockets = ["Semua"] + sorted(uncat_df["pocket"].dropna().unique().tolist())
            col_f1, col_f2, col_f3 = st.columns(3)
            sel_pocket = col_f1.selectbox("Filter Pocket:", pockets)
            sel_type   = col_f2.selectbox("Filter Tipe:", ["Semua", "Expense", "Income", "Transfer"])
            search_kw  = col_f3.text_input("🔍 Cari deskripsi:", placeholder="e.g. Grab, Tokopedia")

            # Apply filters
            filtered = uncat_df.copy()
            if sel_pocket != "Semua":
                filtered = filtered[filtered["pocket"] == sel_pocket]
            if sel_type != "Semua":
                filtered = filtered[filtered["type"] == sel_type]
            if search_kw:
                filtered = filtered[filtered["description"].str.contains(search_kw, case=False, na=False)]

            st.caption(f"Menampilkan **{len(filtered)}** dari {uncat_cnt} transaksi belum dikategorikan.")

            if filtered.empty:
                st.info("Tidak ada transaksi yang sesuai filter.")
            else:
                # Build editable table dengan selectbox kategori
                sub_opts_exp = sorted(cat_df[cat_df["tx_type"] == "Expense"]["sub_category"].tolist())
                sub_opts_inc = sorted(cat_df[cat_df["tx_type"] == "Income"]["sub_category"].tolist())
                sub_opts_tra = sorted(cat_df[cat_df["tx_type"] == "Transfer"]["sub_category"].tolist())

                # Quick-assign: pilih satu kategori untuk semua yang difilter
                with st.container(border=True):
                    st.markdown("**⚡ Quick Assign** — apply kategori yang sama ke semua transaksi di bawah:")
                    qa_col1, qa_col2, qa_col3 = st.columns([2, 2, 1])
                    qa_type = qa_col1.selectbox("Tipe transaksi:", ALL_TYPES, key="qa_type")
                    qa_opts = (sub_opts_exp if qa_type == "Expense"
                               else sub_opts_inc if qa_type == "Income"
                               else sub_opts_tra)
                    qa_sub  = qa_col2.selectbox("Sub-kategori:", qa_opts, key="qa_sub")
                    if qa_col3.button("✅ Apply ke Semua", type="primary", use_container_width=True):
                        updates = [(qa_sub, sub_to_cat(qa_sub, qa_type), int(row["id"]))
                                   for _, row in filtered.iterrows()]
                        bulk_update_categories(updates)
                        st.success(f"✅ {len(updates)} transaksi diupdate ke **{qa_sub}**!")
                        st.rerun()

                st.divider()

                # Tabel per-baris dengan selectbox inline
                st.markdown("**Atau assign per baris:**")

                # Header — tambah kolom Pocket
                h1, h2, h3, h4, h5, h6, h7 = st.columns([1.1, 2.5, 1.3, 1.0, 1.1, 2.2, 0.8])
                h1.markdown("**Tanggal**")
                h2.markdown("**Deskripsi**")
                h3.markdown("**Jumlah**")
                h4.markdown("**Tipe**")
                h5.markdown("**Sumber**")
                h6.markdown("**Sub-Kategori**")
                h7.markdown("**Aksi**")
                st.divider()

                # Simpan state pilihan per baris
                if "bulk_selections" not in st.session_state:
                    st.session_state.bulk_selections = {}

                pending_saves = []

                # Warna pocket badge
                POCKET_COLORS = {
                    "Bank Jago":  ("#f39c12", "#fff8ed"),
                    "Jenius CC":  ("#3498db", "#eef6fd"),
                    "Sinarmas":   ("#e74c3c", "#fef0ef"),
                    "Email":      ("#9b59b6", "#f5f0fb"),
                }

                for _, row in filtered.iterrows():
                    tid     = int(row["id"])
                    tx_type = row["type"]
                    pocket  = str(row.get("pocket", "") or "")
                    sub_list = (sub_opts_exp if tx_type == "Expense"
                                else sub_opts_inc if tx_type == "Income"
                                else sub_opts_tra)

                    c1, c2, c3, c4, c5, c6, c7 = st.columns([1.1, 2.5, 1.3, 1.0, 1.1, 2.2, 0.8])

                    # Tanggal
                    c1.markdown(
                        f"<small>{str(row['date'])[:10]}</small>",
                        unsafe_allow_html=True
                    )

                    # Deskripsi
                    c2.markdown(
                        f"<small title='{row['description']}'>{row['description'][:32]}"
                        f"{'…' if len(row['description']) > 32 else ''}</small>",
                        unsafe_allow_html=True
                    )

                    # Jumlah dengan warna
                    amt_color = "#2ecc71" if tx_type == "Income" else "#e74c3c" if tx_type == "Expense" else "#f39c12"
                    prefix    = "+" if tx_type == "Income" else "-" if tx_type == "Expense" else "→"
                    c3.markdown(
                        f"<small style='color:{amt_color};font-weight:500'>"
                        f"{prefix}{fmt_idr(row['amount'])}</small>",
                        unsafe_allow_html=True
                    )

                    # Badge tipe
                    badge_color = {"Income": "#2ecc71", "Expense": "#e74c3c", "Transfer": "#f39c12"}.get(tx_type, "#888")
                    c4.markdown(
                        f"<small><span style='background:{badge_color}22;color:{badge_color};"
                        f"padding:2px 5px;border-radius:4px;font-size:10px'>{tx_type}</span></small>",
                        unsafe_allow_html=True
                    )

                    # Badge pocket/sumber bank
                    p_fg, p_bg = POCKET_COLORS.get(pocket, ("#555", "#f0f0f0"))
                    pocket_label = pocket[:10] if pocket else "—"
                    c5.markdown(
                        f"<small><span style='background:{p_bg};color:{p_fg};"
                        f"padding:2px 5px;border-radius:4px;font-size:10px;"
                        f"border:1px solid {p_fg}33;white-space:nowrap'>"
                        f"{pocket_label}</span></small>",
                        unsafe_allow_html=True
                    )

                    # Selectbox kategori
                    sel = c6.selectbox(
                        "kat", sub_list, key=f"sel_{tid}",
                        label_visibility="collapsed",
                    )
                    st.session_state.bulk_selections[tid] = (sel, tx_type)

                    # Tombol simpan per baris
                    if c7.button("💾", key=f"save_{tid}", help="Simpan"):
                        pending_saves.append((sel, tx_type, tid))

                    st.markdown("<hr style='margin:2px 0;opacity:0.15'>", unsafe_allow_html=True)

                # Proses simpan per-baris
                if pending_saves:
                    updates = [(s, sub_to_cat(s, t), i) for s, t, i in pending_saves]
                    bulk_update_categories(updates)
                    st.success(f"✅ {len(updates)} transaksi tersimpan!")
                    st.rerun()

                st.divider()

                # Tombol simpan semua
                col_save, col_del, _ = st.columns([2, 2, 3])
                if col_save.button("💾 Simpan Semua Pilihan", type="primary", use_container_width=True):
                    updates = []
                    for _, row in filtered.iterrows():
                        tid = int(row["id"])
                        if tid in st.session_state.bulk_selections:
                            sel_sub, tx_type = st.session_state.bulk_selections[tid]
                            updates.append((sel_sub, sub_to_cat(sel_sub, tx_type), tid))
                    if updates:
                        bulk_update_categories(updates)
                        st.session_state.bulk_selections = {}
                        st.success(f"✅ {len(updates)} transaksi berhasil dikategorikan!")
                        st.rerun()

                if col_del.button("🗑️ Hapus Semua yang Difilter", type="secondary", use_container_width=True):
                    ids = [int(r["id"]) for _, r in filtered.iterrows()]
                    bulk_delete_transactions(ids)
                    st.warning(f"🗑️ {len(ids)} transaksi dihapus.")
                    st.rerun()

        # ════════════════════════════════════════════════════════════════════
        # MODE 2: SATU PER SATU
        # ════════════════════════════════════════════════════════════════════
        with mode_tab2:
            st.markdown("Review detail per transaksi sebelum mengkategorikan.")

            # Navigasi antar transaksi
            if "val_idx" not in st.session_state:
                st.session_state.val_idx = 0

            ids_list = uncat_df["id"].tolist()
            st.session_state.val_idx = min(st.session_state.val_idx, len(ids_list) - 1)
            current_idx = st.session_state.val_idx
            total       = len(ids_list)

            # Progress navigator
            nav1, nav2, nav3, nav4 = st.columns([1, 6, 1, 2])
            if nav1.button("◀", use_container_width=True) and current_idx > 0:
                st.session_state.val_idx -= 1
                st.rerun()
            nav2.progress((current_idx + 1) / total,
                           text=f"Transaksi {current_idx + 1} dari {total}")
            if nav3.button("▶", use_container_width=True) and current_idx < total - 1:
                st.session_state.val_idx += 1
                st.rerun()
            nav4.markdown(f"<div style='text-align:center;padding-top:8px'><b>{current_idx+1}/{total}</b></div>",
                          unsafe_allow_html=True)

            sel_row = uncat_df[uncat_df["id"] == ids_list[current_idx]].iloc[0]

            # Detail card transaksi
            with st.container(border=True):
                d1, d2 = st.columns(2)
                d1.markdown(f"**📅 Tanggal:** {str(sel_row['date'])[:10]}")
                d1.markdown(f"**🏦 Pocket:** {sel_row.get('pocket', '-')}")
                d1.markdown(f"**📂 Sumber:** {sel_row.get('source', 'manual')}")
                d2.markdown(f"**📝 Deskripsi:** {sel_row['description']}")
                d2.markdown(f"**💰 Jumlah:** {fmt_idr(sel_row['amount'])}")
                d2.markdown(f"**🔖 Tipe:** {sel_row['type']}")

            # Pilih kategori
            tx_type = sel_row["type"]
            sub_list = sorted(cat_df[cat_df["tx_type"] == tx_type]["sub_category"].tolist())

            c_sel1, c_sel2 = st.columns(2)
            new_sub = c_sel1.selectbox("📌 Assign ke Sub-Kategori:", sub_list, key="single_sub")
            c_sel2.markdown(f"**Parent category:** {sub_to_cat(new_sub, tx_type)}")
            c_sel2.markdown(f"**Tipe:** {tx_type}")

            btn1, btn2, btn3 = st.columns(3)
            if btn1.button("✅ Sahkan & Lanjut", type="primary", use_container_width=True):
                update_sub_category(int(ids_list[current_idx]), new_sub, tx_type)
                if st.session_state.val_idx < total - 1:
                    st.session_state.val_idx += 1
                else:
                    st.session_state.val_idx = 0
                st.rerun()

            if btn2.button("✅ Sahkan Saja", use_container_width=True):
                update_sub_category(int(ids_list[current_idx]), new_sub, tx_type)
                st.success("Tersahkan!")
                st.rerun()

            if btn3.button("🗑️ Hapus Transaksi", type="secondary", use_container_width=True):
                delete_transaction(int(ids_list[current_idx]))
                st.session_state.val_idx = max(0, current_idx - 1)
                st.rerun()

    st.divider()

    # ── Histori Log (semua transaksi) ─────────────────────────────────────────
    st.markdown("### 📋 Histori Semua Transaksi")

    # Filter histori
    hf1, hf2, hf3 = st.columns(3)
    h_type   = hf1.selectbox("Filter Tipe:", ["Semua", "Expense", "Income", "Transfer"], key="h_type")
    h_status = hf2.selectbox("Filter Status:", ["Semua", "Sudah Dikategorikan", "Belum Dikategorikan"], key="h_status")
    h_search = hf3.text_input("🔍 Cari:", key="h_search")

    hist = df_all.copy()
    if h_type != "Semua":
        hist = hist[hist["type"] == h_type]
    if h_status == "Sudah Dikategorikan":
        hist = hist[hist["sub_category"] != "uncategorized"]
    elif h_status == "Belum Dikategorikan":
        hist = hist[hist["sub_category"] == "uncategorized"]
    if h_search:
        hist = hist[hist["description"].str.contains(h_search, case=False, na=False)]

    st.caption(f"Menampilkan {len(hist)} transaksi")
    display_cols = ["date", "description", "amount", "type", "sub_category", "category", "pocket"]
    display_cols = [c for c in display_cols if c in hist.columns]
    st.dataframe(hist[display_cols], use_container_width=True, height=350, hide_index=True)


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9B — UI: POCKET BALANCE & NET WORTH
# ══════════════════════════════════════════════════════════════════════════════

def tab_pocket_balance(df_all: pd.DataFrame):
    st.title("💼 Saldo & Net Worth")
    st.caption(
        "Pantau saldo aktual per kantong. "
        "Input saldo manual untuk sinkronisasi dengan rekening nyata."
    )

    pb_df = load_pocket_balances()

    # ── Penjelasan logika CC ──────────────────────────────────────────────────
    with st.expander("ℹ️ Cara kerja pencatatan Jenius CC (penting dibaca)", expanded=False):
        st.markdown("""
        **Prinsip yang dipakai (Pendekatan A — Track per Transaksi):**

        | Email | Dicatat sebagai | Alasan |
        |---|---|---|
        | Belanja Yoshinoya Rp 154rb | ✅ **Expense** | Pengeluaran nyata per kategori |
        | Belanja Grab Rp 50rb | ✅ **Expense** | Pengeluaran nyata per kategori |
        | Bayar tagihan CC Rp 3jt | ✅ **Transfer** | Perpindahan uang, BUKAN pengeluaran baru |

        **Kenapa bayar tagihan CC = Transfer?**
        Karena belanja individual sudah tercatat sebagai Expense.
        Kalau bayar tagihan juga Expense → double count (tercatat 2x).

        **Cara cek balance CC:**
        Total hutang CC = Total Expense di Jenius CC - Total Transfer masuk ke CC

        **Input saldo manual** di bawah untuk sinkronisasi dengan saldo rekening nyata.
        """)

    st.divider()

    # ── KPI Net Worth ─────────────────────────────────────────────────────────
    bank_total = pb_df[pb_df["is_cc"] == False]["balance"].sum()
    cc_total   = pb_df[pb_df["is_cc"] == True]["balance"].sum()
    net_worth  = bank_total - cc_total

    k1, k2, k3 = st.columns(3)
    k1.metric("🏦 Total Saldo Bank", fmt_idr(bank_total))
    k2.metric("💳 Total Hutang CC",  fmt_idr(cc_total),
              delta=f"Rp {cc_total:,.0f}".replace(",", ".") + " hutang",
              delta_color="inverse" if cc_total > 0 else "normal")
    k3.metric("💰 Net Worth (Bank - CC)", fmt_idr(net_worth),
              delta_color="normal" if net_worth >= 0 else "inverse")
    st.divider()

    # ── Tabel saldo per pocket ────────────────────────────────────────────────
    st.markdown("### 💳 Saldo per Kantong")
    st.caption("Update saldo manual setiap bulan atau setelah sinkronisasi email.")

    tab_bank, tab_cc = st.tabs(["🏦 Rekening Bank", "💳 Kartu Kredit (CC)"])

    with tab_bank:
        bank_df = pb_df[pb_df["is_cc"] == False].copy()
        if bank_df.empty:
            st.info("Belum ada rekening bank terdaftar.")
        else:
            for _, row in bank_df.iterrows():
                with st.container(border=True):
                    col_name, col_bal, col_btn = st.columns([2, 3, 1])
                    col_name.markdown(f"**{row['pocket_name']}**")
                    col_name.caption(f"Tipe: {row['pocket_type'].upper()}")

                    new_bal = col_bal.number_input(
                        "Saldo saat ini (IDR)",
                        value=float(row["balance"]),
                        min_value=0.0, step=10_000.0,
                        key=f"bal_{row['pocket_name']}",
                        format="%0.0f",
                    )
                    if col_btn.button("💾 Simpan", key=f"sbtn_{row['pocket_name']}", use_container_width=True):
                        update_pocket_balance(row["pocket_name"], new_bal)
                        st.success(f"✅ Saldo {row['pocket_name']} diperbarui!")
                        st.rerun()

                    # Analisis dari transaksi
                    if not df_all.empty:
                        df_p  = df_all[df_all["pocket"] == row["pocket_name"]]
                        total_in  = df_p[df_p["type"] == "Income"]["amount"].sum()
                        total_out = df_p[df_p["type"] == "Expense"]["amount"].sum()
                        total_tf  = df_p[df_p["type"] == "Transfer"]["amount"].sum()
                        col_bal.caption(
                            f"Dari transaksi: Masuk {fmt_idr(total_in)} | "
                            f"Keluar {fmt_idr(total_out)} | "
                            f"Transfer {fmt_idr(total_tf)}"
                        )

        st.divider()
        st.markdown("#### ➕ Tambah Rekening Bank")
        with st.form("add_bank_pocket", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            new_pname  = c1.text_input("Nama Rekening", placeholder="e.g. BCA Tabungan")
            new_ptype  = c2.selectbox("Tipe", ["bank", "e-wallet", "cash", "investasi"])
            new_pbal   = c3.number_input("Saldo Awal (IDR)", min_value=0.0, step=10_000.0)
            if st.form_submit_button("➕ Tambah", type="primary"):
                if new_pname:
                    add_pocket(new_pname, new_ptype, False, new_pbal)
                    st.success(f"✅ {new_pname} ditambahkan!")
                    st.rerun()

    with tab_cc:
        cc_df = pb_df[pb_df["is_cc"] == True].copy()
        if cc_df.empty:
            st.info("Belum ada kartu kredit terdaftar.")
        else:
            for _, row in cc_df.iterrows():
                with st.container(border=True):
                    col_name, col_bal, col_btn = st.columns([2, 3, 1])
                    col_name.markdown(f"**{row['pocket_name']}** 💳")
                    col_name.caption("Kartu Kredit — input tagihan outstanding")

                    new_bal = col_bal.number_input(
                        "Tagihan outstanding saat ini (IDR)",
                        value=float(row["balance"]),
                        min_value=0.0, step=10_000.0,
                        key=f"cc_bal_{row['pocket_name']}",
                        format="%0.0f",
                        help="Isi dengan total tagihan CC yang belum dibayar",
                    )
                    if col_btn.button("💾 Simpan", key=f"ccbtn_{row['pocket_name']}", use_container_width=True):
                        update_pocket_balance(row["pocket_name"], new_bal)
                        st.success(f"✅ Tagihan {row['pocket_name']} diperbarui!")
                        st.rerun()

                    # Analisis hutang CC dari transaksi
                    if not df_all.empty:
                        df_cc    = df_all[df_all["pocket"] == row["pocket_name"]]
                        cc_spend = df_cc[df_cc["type"] == "Expense"]["amount"].sum()
                        cc_paid  = df_all[
                            (df_all["type"] == "Transfer") &
                            (df_all["description"].str.contains("tagihan jenius", case=False, na=False))
                        ]["amount"].sum()
                        col_bal.caption(
                            f"Total belanja CC: {fmt_idr(cc_spend)} | "
                            f"Total sudah dibayar: {fmt_idr(cc_paid)} | "
                            f"Estimasi hutang: {fmt_idr(max(0, cc_spend - cc_paid))}"
                        )

        st.divider()
        st.markdown("#### ➕ Tambah Kartu Kredit")
        with st.form("add_cc_pocket", clear_on_submit=True):
            c1, c2 = st.columns(2)
            new_ccname = c1.text_input("Nama CC", placeholder="e.g. BCA Credit Card")
            new_ccbal  = c2.number_input("Tagihan outstanding (IDR)", min_value=0.0, step=10_000.0)
            if st.form_submit_button("➕ Tambah", type="primary"):
                if new_ccname:
                    add_pocket(new_ccname, "cc", True, new_ccbal)
                    st.success(f"✅ {new_ccname} ditambahkan!")
                    st.rerun()

    st.divider()

    # ── Rekonsiliasi: saldo buku vs saldo manual ──────────────────────────────
    st.markdown("### 🔍 Rekonsiliasi Saldo")
    st.caption("Bandingkan saldo dari transaksi tercatat vs saldo manual yang kamu input.")

    if df_all.empty:
        st.info("Belum ada data transaksi.")
        return

    recon_rows = []
    for _, row in pb_df.iterrows():
        df_p     = df_all[df_all["pocket"] == row["pocket_name"]]
        total_in  = df_p[df_p["type"] == "Income"]["amount"].sum()
        total_out = df_p[df_p["type"] == "Expense"]["amount"].sum()
        total_tf  = df_p[df_p["type"] == "Transfer"]["amount"].sum()
        net_from_tx = total_in - total_out - total_tf
        manual_bal  = row["balance"]
        selisih     = manual_bal - net_from_tx

        recon_rows.append({
            "Kantong":            row["pocket_name"],
            "Tipe":               "CC 💳" if row["is_cc"] else "Bank 🏦",
            "Saldo Manual (IDR)": fmt_idr(manual_bal),
            "Net dari Transaksi": fmt_idr(net_from_tx),
            "Selisih":            fmt_idr(abs(selisih)),
            "Status":             "✅ Match" if abs(selisih) < 1000
                                  else f"⚠️ {'Over' if selisih > 0 else 'Under'} Rp {abs(selisih):,.0f}".replace(",", "."),
        })

    st.dataframe(pd.DataFrame(recon_rows), use_container_width=True, hide_index=True)
    st.caption(
        "Selisih wajar terjadi karena: transaksi tunai tidak tercatat via email, "
        "atau ada transaksi di luar periode sinkronisasi."
    )


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — UI: MASTER DATA MANAGEMENT
# ══════════════════════════════════════════════════════════════════════════════

def tab_master_data():
    st.title("🗂️ Master Data Management")
    st.caption("Kelola kategori, sub-kategori, dan budget bulanan.")

    cat_df = load_categories_df()
    tab_exp, tab_inc, tab_trans = st.tabs(
        ["💸 Expense Categories", "💰 Income Categories", "🔄 Transfer"])

    with tab_exp:
        exp_df = cat_df[cat_df["tx_type"] == "Expense"].copy()
        st.markdown("#### ➕ Tambah / Edit Sub-Kategori Expense")
        with st.form("form_add_exp", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            new_parent = c1.text_input("Parent Category", placeholder="e.g. Health & Wellness")
            new_sub    = c2.text_input("Sub-Category",    placeholder="e.g. vitamins")
            new_budget = c3.number_input("Budget Bulanan (IDR)", min_value=0.0, step=50_000.0)
            if st.form_submit_button("💾 Simpan", type="primary"):
                if new_parent and new_sub:
                    upsert_category("Expense", new_parent, new_sub, new_budget)
                    st.success(f"✅ '{new_sub}' berhasil disimpan!")
                    st.rerun()
                else:
                    st.warning("Parent category dan sub-category wajib diisi.")
        st.divider()
        st.markdown("#### 📋 Daftar Sub-Kategori Expense")
        for parent in sorted(exp_df["parent_category"].unique()):
            grp       = exp_df[exp_df["parent_category"] == parent].reset_index(drop=True)
            total_bud = grp["monthly_budget"].sum()
            with st.expander(f"**{parent}** — Budget total: {fmt_idr(total_bud)}/bln", expanded=True):
                for _, row in grp.iterrows():
                    col_a, col_b, col_c, col_d = st.columns([3, 2, 1, 1])
                    col_a.markdown(f"**{row['sub_category']}**")
                    new_bud = col_b.number_input(
                        "Budget/bln", value=float(row["monthly_budget"]),
                        min_value=0.0, step=50_000.0,
                        key=f"bud_{row['sub_category']}", label_visibility="collapsed")
                    if col_c.button("💾", key=f"save_{row['sub_category']}", help="Simpan budget"):
                        update_budget(row["sub_category"], new_bud)
                        st.success(f"Budget '{row['sub_category']}' diperbarui!")
                        st.rerun()
                    if row["sub_category"] != "uncategorized":
                        if col_d.button("🗑️", key=f"del_{row['sub_category']}", help="Hapus"):
                            delete_category(row["sub_category"])
                            st.rerun()
                    else:
                        col_d.markdown("—")
        st.divider()
        total_all = exp_df["monthly_budget"].sum()
        st.markdown(f"**💡 Total Budget Expense per bulan: {fmt_idr(total_all)}**")
        st.markdown("#### ✏️ Edit Budget Massal")
        edit_df = exp_df[["parent_category", "sub_category", "monthly_budget"]].copy()
        edit_df.columns = ["Parent Category", "Sub-Kategori", "Budget Bulanan (IDR)"]
        edited = st.data_editor(edit_df, use_container_width=True, hide_index=True,
            column_config={
                "Budget Bulanan (IDR)": st.column_config.NumberColumn(
                    "Budget Bulanan (IDR)", min_value=0, step=50000, format="Rp %d"),
                "Parent Category": st.column_config.TextColumn(disabled=True),
                "Sub-Kategori":    st.column_config.TextColumn(disabled=True),
            })
        if st.button("💾 Simpan Semua Perubahan Budget", type="primary"):
            for _, row in edited.iterrows():
                update_budget(row["Sub-Kategori"], float(row["Budget Bulanan (IDR)"]))
            st.success("✅ Semua budget berhasil diperbarui!")
            load_categories_df.clear()
            st.rerun()

    with tab_inc:
        inc_df = cat_df[cat_df["tx_type"] == "Income"].copy()
        st.markdown("#### ➕ Tambah Sub-Kategori Income")
        with st.form("form_add_inc", clear_on_submit=True):
            c1, c2 = st.columns(2)
            new_pi = c1.text_input("Parent Category", placeholder="e.g. Freelance")
            new_si = c2.text_input("Sub-Category",    placeholder="e.g. project fee")
            if st.form_submit_button("💾 Simpan", type="primary"):
                if new_pi and new_si:
                    upsert_category("Income", new_pi, new_si, 0)
                    st.success(f"✅ '{new_si}' berhasil disimpan!")
                    st.rerun()
                else:
                    st.warning("Semua field wajib diisi.")
        st.divider()
        for _, row in inc_df.iterrows():
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{row['sub_category']}** — _{row['parent_category']}_")
            if c2.button("🗑️ Hapus", key=f"del_inc_{row['sub_category']}"):
                delete_category(row["sub_category"])
                st.rerun()

    with tab_trans:
        tra_df = cat_df[cat_df["tx_type"] == "Transfer"].copy()
        st.dataframe(tra_df[["parent_category", "sub_category"]].rename(
            columns={"parent_category": "Parent", "sub_category": "Sub-Kategori"}),
            use_container_width=True, hide_index=True)
        with st.form("form_add_tra", clear_on_submit=True):
            new_st = st.text_input("Tambah Sub-Category Transfer")
            if st.form_submit_button("💾 Simpan"):
                if new_st:
                    upsert_category("Transfer", "Internal Transfer", new_st, 0)
                    st.success(f"✅ '{new_st}' ditambahkan!")
                    st.rerun()


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — UI: EMAIL SYNC
# ══════════════════════════════════════════════════════════════════════════════

def tab_email_sync():
    st.title("📧 Sinkronisasi Email Bank")
    st.caption("Tarik otomatis transaksi dari notifikasi email Bank Jago, Jenius, dan Sinarmas.")

    with st.expander("ℹ️ Cara setup Gmail App Password", expanded=False):
        st.markdown("""
        1. Login ke Gmail personal (`sintawuln@gmail.com`)
        2. Aktifkan 2FA di [myaccount.google.com/security](https://myaccount.google.com/security)
        3. Buka [myaccount.google.com/apppasswords](https://myaccount.google.com/apppasswords)
        4. Buat App Password → beri nama `CFO Console` → salin 16 karakter
        5. Tambahkan ke **Streamlit Secrets**:
        ```toml
        GMAIL_EMAIL = "sintawuln@gmail.com"
        GMAIL_APP_PASSWORD = "xxxxxxxxxxxxxxxx"
        ```
        6. Set email notifikasi bank ke `sintawuln@gmail.com` di app Bank Jago / Jenius / Sinarmas
        """)

    st.divider()

    with st.container(border=True):
        st.markdown("#### 🔐 Konfigurasi Koneksi Gmail")
        has_secrets = ("GMAIL_EMAIL" in st.secrets and "GMAIL_APP_PASSWORD" in st.secrets)
        if has_secrets:
            email_addr   = st.secrets["GMAIL_EMAIL"]
            app_password = st.secrets["GMAIL_APP_PASSWORD"]
            st.success(f"✅ Kredensial tersimpan di Secrets. Akun: **{email_addr}**")
        else:
            st.info("💡 Simpan kredensial di Streamlit Secrets untuk keamanan lebih baik.")
            c1, c2       = st.columns(2)
            email_addr   = c1.text_input("Gmail Address", value="sintawuln@gmail.com")
            app_password = c2.text_input("App Password", type="password",
                                          placeholder="xxxx xxxx xxxx xxxx")
        # ── Rentang Waktu ─────────────────────────────────────────────────────
        st.markdown("#### 📅 Rentang Waktu Sinkronisasi")

        import datetime as dt_mod

        today     = datetime.now().date()
        this_mon  = today.replace(day=1)

        # Quick-select buttons
        st.markdown("**⚡ Pilih cepat:**")
        qc = st.columns(6)
        QUICK = [
            ("Hari ini",      today,                           today),
            ("7 hari",        today - dt_mod.timedelta(days=6), today),
            ("14 hari",       today - dt_mod.timedelta(days=13), today),
            ("30 hari",       today - dt_mod.timedelta(days=29), today),
            ("Bulan ini",     this_mon,                        today),
            ("Bulan lalu",
             (this_mon - dt_mod.timedelta(days=1)).replace(day=1),
             this_mon - dt_mod.timedelta(days=1)),
        ]

        if "sync_from" not in st.session_state:
            st.session_state.sync_from = today - dt_mod.timedelta(days=6)
        if "sync_to" not in st.session_state:
            st.session_state.sync_to = today

        for i, (label, qfrom, qto) in enumerate(QUICK):
            if qc[i].button(label, use_container_width=True, key=f"qb_{i}"):
                st.session_state.sync_from = qfrom
                st.session_state.sync_to   = qto
                st.rerun()

        # Date range manual input
        st.markdown("**🗓️ Atau pilih tanggal manual:**")
        dc1, dc2 = st.columns(2)
        date_from = dc1.date_input(
            "Dari tanggal",
            value=st.session_state.sync_from,
            max_value=today,
            key="di_from",
        )
        date_to = dc2.date_input(
            "Sampai tanggal",
            value=st.session_state.sync_to,
            max_value=today,
            key="di_to",
        )

        if date_from > date_to:
            st.warning("⚠️ Tanggal awal harus sebelum tanggal akhir.")
            date_from, date_to = date_to, date_from

        # Sync state ke session
        st.session_state.sync_from = date_from
        st.session_state.sync_to   = date_to

        days_back  = (date_to - date_from).days + 1

        # Summary bar
        duration_label = (
            "Hari ini" if days_back == 1 and date_from == today
            else f"{days_back} hari"
        )
        r1, r2, r3 = st.columns(3)
        r1.metric("📅 Dari",     date_from.strftime("%d %b %Y"))
        r2.metric("📅 Sampai",   date_to.strftime("%d %b %Y"))
        r3.metric("⏱️ Durasi",   duration_label)

        range_info = (
            f"Hari ini ({today.strftime('%d %b %Y')})"
            if days_back == 1 and date_from == today
            else f"{date_from.strftime('%d %b')} — {date_to.strftime('%d %b %Y')} ({days_back} hari)"
        )

        # Anti-duplikat info
        st.success("🛡️ **Anti-duplikat aktif** — aman dijalankan berkali-kali. Transaksi yang sudah ada di database akan otomatis dilewati.")

    st.divider()

    col1, col2, col3 = st.columns([1, 1, 2])
    do_fetch   = col1.button("🔄 Cek & Simpan",   type="primary", use_container_width=True)
    do_preview = col2.button("👁️ Preview Saja",    use_container_width=True)
    if col3.button("🔃 Refresh Data",              use_container_width=True):
        load_all_transactions.clear()
        st.success("✅ Data berhasil di-refresh!")
        st.rerun()

    if do_fetch or do_preview:
        if not email_addr or not app_password:
            st.warning("Masukkan Gmail address dan App Password terlebih dahulu.")
            st.stop()

        with st.spinner(f"Menghubungkan ke Gmail · {range_info}..."):
            txs, raw_emails, err = fetch_email_transactions(email_addr, app_password, days_back)

        if err:
            st.error(f"⚠️ Error: {err}")

        col_a, col_b = st.columns(2)
        col_a.metric("📬 Email ditemukan", len(raw_emails))
        col_b.metric("💳 Transaksi di-parse", len(txs))

        if not txs:
            st.warning("Tidak ada transaksi berhasil di-parse.")
            if raw_emails:
                with st.expander("📋 Raw email (debug)"):
                    for em in raw_emails[:5]:
                        st.text(f"From   : {em['from']}")
                        st.text(f"Subject: {em['subject']}")
                        st.text(f"Date   : {em['date']}")
                        st.text(f"Body   : {em['body'][:200]}")
                        st.divider()
        else:
            df_preview = pd.DataFrame(txs)
            st.markdown("#### 👀 Preview Transaksi Terdeteksi")
            display_cols = [c for c in ["date", "description", "amount", "type", "pocket"] if c in df_preview.columns]
            st.dataframe(df_preview[display_cols], use_container_width=True, hide_index=True)

            if do_fetch:
                with st.spinner("Menyimpan ke database Supabase..."):
                    inserted, skipped = save_transactions(df_preview)
                if inserted > 0:
                    st.success(
                        f"✅ **{inserted}** transaksi baru disimpan, "
                        f"**{skipped}** dilewati (sudah ada / duplikat)."
                    )
                    st.info("💡 Pergi ke **⚙️ Validasi Antrean** untuk mengkategorisasi transaksi baru.")
                else:
                    st.success(
                        f"✅ Sinkronisasi selesai — tidak ada transaksi baru. "
                        f"**{skipped}** transaksi sudah ada di database. "
                        f"Aman dijalankan ulang kapan saja."
                    )
            else:
                st.info("Mode preview — belum disimpan. Klik **'Cek & Simpan'** untuk menyimpan.")

    st.divider()
    st.markdown("#### 📊 Histori Transaksi dari Email")
    df_all = load_all_transactions()
    if not df_all.empty and "source" in df_all.columns:
        email_txs = df_all[df_all["source"] == "email"].copy()
        if not email_txs.empty:
            st.metric("Total transaksi dari email", len(email_txs))
            st.dataframe(
                email_txs[["date", "description", "amount", "type", "sub_category", "pocket"]],
                use_container_width=True, height=250, hide_index=True)
        else:
            st.info("Belum ada transaksi dari email.")
    else:
        st.info("Belum ada data dari email.")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 11 — UI: INGESTION DATA
# ══════════════════════════════════════════════════════════════════════════════

def tab_ingestion():
    st.title("📥 Ingestion Data")
    tab_pdf, tab_manual = st.tabs(["📄 Upload PDF Statement", "✍️ Input Manual"])

    with tab_pdf:
        st.markdown("Unggah file PDF e-Statement dari bank Anda.")
        with st.form("pdf_form", clear_on_submit=True):
            bank_type    = st.selectbox("Pilih Institusi / Bank", ["Sinarmas", "Jenius CC", "Bank Jago"])
            pdf_file     = st.file_uploader("Pilih File PDF", type=["pdf"])
            pdf_password = st.text_input("Password PDF (Jika dikunci)", type="password")
            if st.form_submit_button("🚀 Ekstrak PDF", type="primary"):
                if pdf_file:
                    with st.spinner("Membongkar brankas PDF..."):
                        df_extracted, error = extract_pdf_data(pdf_file, pdf_password, bank_type)
                        if error:
                            st.error(f"Gagal membaca PDF. Error: {error}")
                        elif df_extracted is not None and not df_extracted.empty:
                            ins, skp = save_transactions(df_extracted)
                            st.success(f"✅ {ins} transaksi baru, {skp} dilewati (duplikat).")
                        else:
                            st.warning("Tidak ada transaksi ditemukan.")
                else:
                    st.warning("Mohon unggah file PDF terlebih dahulu.")
        st.caption("Engine PDF berbasis pattern matching.")

    with tab_manual:
        with st.form("manual_form", clear_on_submit=True):
            c1, c2   = st.columns(2)
            m_type   = c1.selectbox("Tipe Transaksi", ALL_TYPES)
            m_date   = c1.date_input("Tanggal", value=date.today())
            m_amount = c1.number_input("Jumlah (IDR)", min_value=0.0, step=10_000.0)
            m_pocket = c2.text_input("Sumber Kantong", placeholder="e.g. Cash, BCA")
            m_desc   = c2.text_input("Deskripsi")
            df_cat   = load_categories_df()
            sub_opts = sorted(df_cat[df_cat["tx_type"] == m_type]["sub_category"].tolist())
            m_sub    = c2.selectbox("Sub-Kategori", sub_opts)
            if st.form_submit_button("💾 Simpan Manual"):
                manual_df = pd.DataFrame([{
                    "date": m_date.strftime("%Y-%m-%d"), "description": m_desc,
                    "amount": m_amount, "type": m_type,
                    "sub_category": m_sub, "category": sub_to_cat(m_sub, m_type),
                    "pocket": m_pocket, "source": "manual",
                }])
                save_transactions(manual_df)
                st.success("Tersimpan!")


# ══════════════════════════════════════════════════════════════════════════════
# SECTION 12 — MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st.set_page_config(
        page_title="CFO Console 7.0", page_icon="🏦",
        layout="wide", initial_sidebar_state="expanded",
    )
    init_db()

    if "current_nav" not in st.session_state:
        st.session_state.current_nav = "📊 Dashboard"

    st.sidebar.title("🏦 Holistic Wealth")
    st.sidebar.caption("The CFO Console Cloud v7.0")
    st.sidebar.divider()

    nav = st.sidebar.radio(
        "Navigasi Utama",
        ["📊 Dashboard", "🎯 Budget vs Actual", "💼 Saldo & Net Worth",
         "🗂️ Master Data", "📧 Email Sync", "⚙️ Validasi Antrean", "📥 Ingestion Data"],
        key="current_nav",
    )

    df_all = load_all_transactions()

    if nav == "📊 Dashboard":
        tab_dashboard(df_all)
    elif nav == "🎯 Budget vs Actual":
        tab_budget_vs_actual(df_all)
    elif nav == "💼 Saldo & Net Worth":
        tab_pocket_balance(df_all)
    elif nav == "🗂️ Master Data":
        tab_master_data()
    elif nav == "📧 Email Sync":
        tab_email_sync()
    elif nav == "⚙️ Validasi Antrean":
        tab_validation(df_all)
    elif nav == "📥 Ingestion Data":
        tab_ingestion()


if __name__ == "__main__":
    main()
