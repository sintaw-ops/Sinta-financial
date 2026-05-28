import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import io
import re
from datetime import datetime, date
import plotly.express as px
import plotly.graph_objects as go
import pdfplumber

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS & CONFIGURATION
# ══════════════════════════════════════════════════════════════════════════════

MONTHLY_BUDGET = 11_500_000   
ALL_TYPES = ["Income", "Expense", "Transfer"] 

CATEGORY_SEED = [
    ("Income",   "Salary",                 "salary",               0), 
    ("Income",   "Bonus",                  "bonus / thr",          0), 
    ("Income",   "Investment Return",      "dividen",              0), 
    ("Income",   "Investment Return",      "bunga tabungan",       0), 
    ("Income",   "Other Income",           "other income",         0), 
    ("Expense",  "Essential Living",       "food",           2_500_000), 
    ("Expense",  "Essential Living",       "transport",      1_000_000), 
    ("Expense",  "Essential Living",       "utility",          500_000), 
    ("Expense",  "Health & Wellness",      "sports",         1_500_000), 
    ("Expense",  "Health & Wellness",      "nutritions",     1_000_000), 
    ("Expense",  "Health & Wellness",      "medical care",     500_000), 
    ("Expense",  "Family & Social",        "family",         2_000_000), 
    ("Expense",  "Family & Social",        "donation",         500_000), 
    ("Expense",  "Family & Social",        "team gathering",   300_000), 
    ("Expense",  "Lifestyle & Personal Care", "shopping",    1_000_000), 
    ("Expense",  "Lifestyle & Personal Care", "skincare and make up", 750_000), 
    ("Expense",  "Education & Growth",     "education",        500_000), 
    ("Expense",  "Wealth & Sinking Fund",  "invest gold",    2_000_000), 
    ("Expense",  "Wealth & Sinking Fund",  "stock investment", 1_000_000), 
    ("Expense",  "Wealth & Sinking Fund",  "emergency",      1_000_000), 
    ("Expense",  "Wealth & Sinking Fund",  "gift",             300_000), 
    ("Expense",  "Wealth & Sinking Fund",  "holiday fund",   2_000_000), 
    ("Expense",  "Uncategorized",          "uncategorized",          0), 
    ("Transfer", "Internal Transfer",      "internal transfer",      0), 
]

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE LAYER
# ══════════════════════════════════════════════════════════════════════════════

def get_supabase_conn():
    db_uri = f"postgresql://{st.secrets['DB_USER']}:{st.secrets['DB_PASSWORD']}@{st.secrets['DB_HOST']}:{st.secrets['DB_PORT']}/{st.secrets['DB_NAME']}?sslmode=require"
    return psycopg2.connect(db_uri, connect_timeout=10)

def init_db():
    conn = get_supabase_conn()
    c = conn.cursor()
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
    c.execute("SELECT COUNT(*) FROM categories")
    if c.fetchone()[0] == 0:
        execute_values(c, "INSERT INTO categories (tx_type, parent_category, sub_category, monthly_budget) VALUES %s ON CONFLICT DO NOTHING", CATEGORY_SEED)
    conn.commit()
    c.close()
    conn.close()

@st.cache_data
def load_categories_df():
    conn = get_supabase_conn()
    df = pd.read_sql_query("SELECT * FROM categories", conn)
    conn.close()
    return df

@st.cache_data
def get_sub_to_cat_map():
    df = load_categories_df()
    return dict(zip(df["sub_category"].str.lower(), df["parent_category"]))

def sub_to_cat(sub: str, tx_type: str) -> str:
    if tx_type == "Transfer": return "Internal Transfer" 
    mapping = get_sub_to_cat_map()
    return mapping.get(sub.lower(), "Other Income" if tx_type == "Income" else "Uncategorized")

@st.cache_data
def load_all_transactions():
    conn = get_supabase_conn()
    df = pd.read_sql_query("SELECT * FROM transactions ORDER BY date DESC", conn)
    conn.close()
    if not df.empty:
        df["date"] = pd.to_datetime(df["date"])
    return df

def save_transactions(df: pd.DataFrame) -> tuple[int, int]:
    conn = get_supabase_conn()
    c = conn.cursor()
    inserted = skipped = 0
    for _, row in df.iterrows():
        sub = str(row.get("sub_category", "uncategorized")).strip()
        tx_type = str(row["type"])
        cat = sub_to_cat(sub, tx_type)
        try:
            c.execute(
                """INSERT INTO transactions (date, description, amount, type, category, sub_category, pocket) 
                   VALUES (%s, %s, %s, %s, %s, %s, %s) ON CONFLICT (date, description, amount) DO NOTHING""",
                (str(row["date"])[:10], str(row["description"]), float(row["amount"]), tx_type, cat, sub, str(row.get("pocket", "")))
            )
            if c.rowcount > 0: inserted += 1
            else: skipped += 1
        except Exception:
            skipped += 1
    conn.commit()
    c.close()
    conn.close()
    return inserted, skipped

def update_sub_category(tid: int, new_sub: str, tx_type: str):
    conn = get_supabase_conn()
    c = conn.cursor()
    c.execute("UPDATE transactions SET sub_category=%s, category=%s WHERE id=%s", (new_sub, sub_to_cat(new_sub, tx_type), tid))
    conn.commit()
    c.close()
    conn.close()

def delete_transaction(tid: int):
    conn = get_supabase_conn()
    c = conn.cursor()
    c.execute("DELETE FROM transactions WHERE id = %s", (tid,))
    conn.commit()
    c.close()
    conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PDF PARSER ENGINE
# ══════════════════════════════════════════════════════════════════════════════

def extract_pdf_data(file, password, bank_type):
    """Fungsi ekstraksi dasar untuk membaca baris transaksi PDF."""
    transactions = []
    try:
        with pdfplumber.open(file, password=password if password else None) as pdf:
            for page in pdf.pages:
                text = page.extract_text()
                if not text: continue
                lines = text.split('\n')
                
                for line in lines:
                    # Logika RegEx Sederhana berdasarkan pola Bank
                    if bank_type == "Sinarmas":
                        # Mencari pola: DD Mmm YYYY ... angka.00
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+(.+?)\s+([\d\,\.]+)\s*$', line)
                        if match:
                            amt_str = match.group(3).replace(',', '')
                            if amt_str.count('.') > 1: amt_str = amt_str.rsplit('.', 1)[0] # clean up format
                            amount = float(amt_str)
                            # Cek Debet atau Kredit (Asumsi sederhana: jika ada tulisan auto transfer/incoming = income)
                            tx_type = "Income" if "Incoming" in line or "Credit" in line else "Expense"
                            transactions.append({"date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"), "description": match.group(2)[:40], "amount": amount, "type": tx_type, "pocket": "Sinarmas", "sub_category": "uncategorized", "category": "Uncategorized"})

                    elif bank_type == "Jenius CC":
                        # Pola: DD Mmm YYYY DD Mmm YYYY DESKRIPSI JUMLAH
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+\d{2}\s[a-zA-Z]{3}\s\d{4}\s+(.+?)\s+([\d\,\.]+)(?:\sCR)?$', line)
                        if match:
                            amount = float(match.group(3).replace(',', ''))
                            tx_type = "Income" if "CR" in line or "Pembayaran" in line else "Expense"
                            transactions.append({"date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"), "description": match.group(2)[:40], "amount": amount, "type": tx_type, "pocket": "Jenius CC", "sub_category": "uncategorized", "category": "Uncategorized"})
                            
                    elif bank_type == "Bank Jago":
                        # Pola Jago: DD Mmm YYYY HH.MM Deskripsi Jumlah
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+\d{2}\.\d{2}\s+(.+?)\s+([\-\+])([\d\.]+)', line)
                        if match:
                            amount = float(match.group(4).replace('.', ''))
                            tx_type = "Income" if match.group(3) == "+" else "Expense"
                            transactions.append({"date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"), "description": match.group(2)[:40], "amount": amount, "type": tx_type, "pocket": "Bank Jago", "sub_category": "uncategorized", "category": "Uncategorized"})
                            
    except Exception as e:
        return None, str(e)
    
    return pd.DataFrame(transactions), None

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — UI TABS
# ══════════════════════════════════════════════════════════════════════════════

def fmt_idr(v: float) -> str: return f"Rp {v:,.0f}".replace(",", ".")

def tab_dashboard(df_all: pd.DataFrame):
    st.title("📊 Financial Dashboard")
    if df_all.empty:
        st.info("Belum ada data. Silakan upload PDF Statement atau input manual.")
        return

    df_clean = df_all[(df_all["type"] != "Transfer") & (df_all["sub_category"] != "uncategorized")].copy()
    df_clean["year"] = df_clean["date"].dt.year
    df_clean["month"] = df_clean["date"].dt.month
    df_clean["quarter"] = df_clean["date"].dt.to_period("Q").astype(str)

    # 🎛️ Modern Filter Container
    with st.container(border=True):
        col1, col2, col3 = st.columns(3)
        sel_year = col1.selectbox("Filter Tahun", sorted(df_clean["year"].unique(), reverse=True))
        view_mode = col2.radio("Mode Tampilan", ["Bulanan", "Kuartalan"], horizontal=True)
        
        df_year = df_clean[df_clean["year"] == sel_year]
        if view_mode == "Bulanan":
            avail_months = sorted(df_year["month"].unique())
            sel_months = col3.multiselect("Bulan", avail_months, default=avail_months, format_func=lambda m: datetime(2000, m, 1).strftime("%B"))
            df_filtered = df_year[df_year["month"].isin(sel_months)]
            time_group = "date"
        else:
            avail_q = sorted(df_year["quarter"].unique())
            sel_q = col3.multiselect("Kuartal", avail_q, default=avail_q)
            df_filtered = df_year[df_year["quarter"].isin(sel_q)]
            df_filtered["year_month"] = df_filtered["date"].dt.to_period("M").astype(str)
            time_group = "year_month"

    if df_filtered.empty:
        st.warning("Tidak ada data untuk filter yang dipilih.")
        return

    inc_df = df_filtered[df_filtered["type"] == "Income"]
    exp_df = df_filtered[df_filtered["type"] == "Expense"]
    ti, te = inc_df["amount"].sum(), exp_df["amount"].sum()

    # 📈 KPI Metrics
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("💰 Pemasukan", fmt_idr(ti))
    c2.metric("💸 Pengeluaran", fmt_idr(te))
    c3.metric("🏦 Net Income", fmt_idr(ti - te))
    c4.metric("🔥 Burn Rate", f"{(te/ti*100) if ti>0 else 0:.1f}%")
    st.divider()

    # 📊 Chart Section
    st.markdown("### 📉 Tren Pengeluaran vs Pemasukan")
    
    # 1. Line Chart: Expenses/Income over Time
    line_data_exp = exp_df.groupby(time_group)["amount"].sum().reset_index()
    line_data_inc = inc_df.groupby(time_group)["amount"].sum().reset_index()
    
    fig_line = go.Figure()
    fig_line.add_trace(go.Scatter(x=line_data_exp[time_group], y=line_data_exp["amount"], mode='lines+markers', name='Pengeluaran', line=dict(color='#e74c3c', width=3)))
    fig_line.add_trace(go.Scatter(x=line_data_inc[time_group], y=line_data_inc["amount"], mode='lines+markers', name='Pemasukan', line=dict(color='#2ecc71', width=3)))
    fig_line.update_layout(plot_bgcolor="rgba(0,0,0,0)", hovermode="x unified", margin=dict(l=0, r=0, t=30, b=0))
    st.plotly_chart(fig_line, use_container_width=True)

    col_chart1, col_chart2 = st.columns(2)
    
    with col_chart1:
        st.markdown("#### 💵 Sumber Pemasukan")
        inc_bar = inc_df.groupby("sub_category")["amount"].sum().reset_index().sort_values("amount", ascending=True)
        fig_inc = px.bar(inc_bar, x="amount", y="sub_category", orientation='h', color_discrete_sequence=['#3498db'])
        fig_inc.update_layout(plot_bgcolor="rgba(0,0,0,0)", xaxis_title="", yaxis_title="")
        st.plotly_chart(fig_inc, use_container_width=True)

    with col_chart2:
        st.markdown("#### 🏆 Alokasi Savings & Investasi")
        sav_df = df_filtered[df_filtered["category"] == "Wealth & Sinking Fund"]
        sav_bar = sav_df.groupby("sub_category")["amount"].sum().reset_index().sort_values("amount", ascending=True)
        if not sav_bar.empty:
            fig_sav = px.bar(sav_bar, x="amount", y="sub_category", orientation='h', color_discrete_sequence=['#f1c40f'])
            fig_sav.update_layout(plot_bgcolor="rgba(0,0,0,0)", xaxis_title="", yaxis_title="")
            st.plotly_chart(fig_sav, use_container_width=True)
        else:
            st.info("Belum ada alokasi saving di periode ini.")

def tab_ingestion():
    st.title("📥 Ingestion Data")
    
    tab_pdf, tab_manual = st.tabs(["📄 Upload PDF Statement", "✍️ Input Manual"])
    
    with tab_pdf:
        st.markdown("Unggah file PDF e-Statement dari bank Anda. Sistem akan memindai transaksi otomatis.")
        with st.form("pdf_form", clear_on_submit=True):
            bank_type = st.selectbox("Pilih Institusi / Bank", ["Sinarmas", "Jenius CC", "Bank Jago"])
            pdf_file = st.file_uploader("Pilih File PDF", type=["pdf"])
            pdf_password = st.text_input("Password PDF (Jika dokumen dikunci)", type="password", help="Contoh: 23092000 untuk Sinarmas")
            
            if st.form_submit_button("🚀 Ekstrak PDF", type="primary"):
                if pdf_file:
                    with st.spinner("Membongkar brankas PDF..."):
                        df_extracted, error = extract_pdf_data(pdf_file, pdf_password, bank_type)
                        if error:
                            st.error(f"Gagal membaca PDF. Pastikan password benar. Error: {error}")
                        elif df_extracted is not None and not df_extracted.empty:
                            ins, skp = save_transactions(df_extracted)
                            st.success(f"✅ Sukses! {ins} transaksi baru ditambahkan, {skp} dilewati (duplikat).")
                            st.cache_data.clear()
                        else:
                            st.warning("Tidak ada transaksi yang cocok dengan format pemindai ditemukan.")
                else:
                    st.warning("Mohon unggah file PDF terlebih dahulu.")
                    
        st.caption("Catatan: Engine PDF bekerja berdasarkan *pattern matching*. Jika format bank berubah, beberapa baris mungkin lolos.")

    with tab_manual:
        with st.form("manual_form", clear_on_submit=True):
            c1, c2 = st.columns(2)
            m_type = c1.selectbox("Tipe Transaksi", ALL_TYPES)
            m_date = c1.date_input("Tanggal", value=date.today())
            m_amount = c1.number_input("Jumlah (IDR)", min_value=0.0, step=10_000.0)
            m_pocket = c2.text_input("Sumber Kantong", placeholder="e.g. Cash, BCA")
            m_desc = c2.text_input("Deskripsi")
            
            df_cat = load_categories_df()
            sub_opts = sorted(df_cat[df_cat["tx_type"] == m_type]["sub_category"].tolist())
            m_sub = c2.selectbox("Sub-Kategori", sub_opts)
            
            if st.form_submit_button("💾 Simpan Manual"):
                manual_df = pd.DataFrame([{"date": m_date.strftime("%Y-%m-%d"), "description": m_desc, "amount": m_amount, "type": m_type, "sub_category": m_sub, "category": sub_to_cat(m_sub, m_type), "pocket": m_pocket}])
                save_transactions(manual_df)
                st.success("Tersimpan!")
                st.cache_data.clear()

def tab_validation(df_all: pd.DataFrame):
    st.title("⚙️ Kelola & Validasi Data")
    
    uncat_df = df_all[df_all["sub_category"] == "uncategorized"].copy()
    if not uncat_df.empty:
        st.warning(f"⚠️ Terdapat **{len(uncat_df)}** transaksi mentah hasil robot/PDF yang butuh disahkan!")
        
        sel_id = st.selectbox(
            "Pilih Transaksi Mentah:", 
            uncat_df["id"].tolist(), 
            format_func=lambda i: f"ID {i} | {uncat_df[uncat_df['id']==i]['date'].values[0][:10]} | {fmt_idr(uncat_df[uncat_df['id']==i]['amount'].values[0])} | {uncat_df[uncat_df['id']==i]['description'].values[0][:30]}"
        )
        sel_row = uncat_df[uncat_df["id"] == sel_id].iloc[0]
        
        df_cat = load_categories_df()
        opts = sorted(df_cat[df_cat["tx_type"] == sel_row["type"]]["sub_category"].tolist())
        
        new_sub = st.selectbox("Sahkan ke Sub-Kategori:", opts)
        
        col1, col2 = st.columns(2)
        if col1.button("✅ Sahkan Transaksi", type="primary", use_container_width=True):
            update_sub_category(sel_id, new_sub, sel_row["type"])
            st.success("Tersahkan!")
            st.cache_data.clear()
            st.rerun()
        if col2.button("🗑️ Hapus Transaksi", type="secondary", use_container_width=True):
            delete_transaction(sel_id)
            st.cache_data.clear()
            st.rerun()
    else:
        st.success("🎉 Seluruh data transaksi sudah bersih dan terkategorisasi!")

    st.divider()
    st.markdown("### 📋 Database Histori Log")
    st.dataframe(df_all[["date", "description", "amount", "type", "sub_category", "pocket"]], use_container_width=True, height=300)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st.set_page_config(page_title="CFO Console 6.0", page_icon="🏦", layout="wide", initial_sidebar_state="expanded")
    init_db()

    # Navigasi tersinkronisasi menggunakan Session State
    if "current_nav" not in st.session_state:
        st.session_state.current_nav = "📊 Dashboard"

    st.sidebar.title("🏦 Holistic Wealth")
    st.sidebar.caption("The CFO Console Cloud v6.0")
    st.sidebar.divider()

    # Sidebar Radio yang mem-bind langsung ke session state
    nav_selection = st.sidebar.radio(
        "Navigasi Utama", 
        ["📊 Dashboard", "⚙️ Validasi Antrean", "📥 Ingestion Data"], 
        key="current_nav"
    )

    df_all = load_all_transactions()

    # Render sesuai pilihan navigasi
    if nav_selection == "📊 Dashboard":
        tab_dashboard(df_all)
    elif nav_selection == "📥 Ingestion Data":
        tab_ingestion()
    elif nav_selection == "⚙️ Validasi Antrean":
        tab_validation(df_all)

if __name__ == "__main__":
    main()
