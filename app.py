╔══════════════════════════════════════════════════════════════════════════════╗
║   Personal Finance Dashboard  —  v5.1 "The CFO Console Cloud"              ║
║   Supabase Backend · Pintu Satu Automation Compatible · Data Cleansing       ║
║   Multi-bank · Manual Input · CFO-level Analytics · Sub-Category Budgets   ║
╚══════════════════════════════════════════════════════════════════════════════╝

import streamlit as st
import pandas as pd
import psycopg2
from psycopg2.extras import execute_values
import io
from datetime import datetime, date
import plotly.express as px
import plotly.graph_objects as go

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 1 — CONSTANTS
# ══════════════════════════════════════════════════════════════════════════════

MONTHLY_BUDGET = 11_500_000   # Total budget bulanan keseluruhan
ALL_TYPES = ["Income", "Expense", "Transfer"]

# Seed data untuk tabel categories master data
CATEGORY_SEED = [
    # Income
    ("Income",   "Salary",                 "salary",               0),
    ("Income",   "Bonus",                  "bonus / thr",          0),
    ("Income",   "Investment Return",      "dividen",              0),
    ("Income",   "Investment Return",      "bunga tabungan",       0),
    ("Income",   "Other Income",           "other income",         0),
    # Expense — Essential Living
    ("Expense",  "Essential Living",       "food",           2_500_000),
    ("Expense",  "Essential Living",       "transport",      1_000_000),
    ("Expense",  "Essential Living",       "utility",          500_000),
    # Expense — Health & Wellness
    ("Expense",  "Health & Wellness",      "sports",         1_500_000),
    ("Expense",  "Health & Wellness",      "nutritions",     1_000_000),
    ("Expense",  "Health & Wellness",      "medical care",     500_000),
    # Expense — Family & Social
    ("Expense",  "Family & Social",        "family",         2_000_000),
    ("Expense",  "Family & Social",        "donation",         500_000),
    ("Expense",  "Family & Social",        "team gathering",   300_000),
    # Expense — Lifestyle & Personal Care
    ("Expense",  "Lifestyle & Personal Care", "shopping",    1_000_000),
    ("Expense",  "Lifestyle & Personal Care", "skincare and make up", 750_000),
    # Expense — Education & Growth
    ("Expense",  "Education & Growth",     "education",        500_000),
    # Expense — Wealth & Sinking Fund
    ("Expense",  "Wealth & Sinking Fund",  "invest gold",    2_000_000),
    ("Expense",  "Wealth & Sinking Fund",  "stock investment", 1_000_000),
    ("Expense",  "Wealth & Sinking Fund",  "emergency",      1_000_000),
    ("Expense",  "Wealth & Sinking Fund",  "gift",             300_000),
    ("Expense",  "Wealth & Sinking Fund",  "holiday fund",   2_000_000),
    # Expense — Uncategorized
    ("Expense",  "Uncategorized",          "uncategorized",          0),
    # Transfer (internal)
    ("Transfer", "Internal Transfer",      "internal transfer",      0),
]

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE LAYER (SUPABASE CLOUD PERSISTENT)
# ══════════════════════════════════════════════════════════════════════════════

def get_supabase_conn():
    """Membuka koneksi aman ke database Supabase menggunakan Secrets Streamlit."""
    return psycopg2.connect(
        host=st.secrets["DB_HOST"],
        database=st.secrets["DB_NAME"],
        user=st.secrets["DB_USER"],
        password=st.secrets["DB_PASSWORD"],
        port=st.secrets["DB_PORT"]
    )

def init_db():
    """Membuat tabel jika belum ada dan melakukan seeding master data kategori."""
    conn = get_supabase_conn()
    c = conn.cursor()

    # Tabel categories
    c.execute("""
        CREATE TABLE IF NOT EXISTS categories (
            id SERIAL PRIMARY KEY,
            tx_type TEXT NOT NULL,
            parent_category TEXT NOT NULL,
            sub_category TEXT NOT NULL UNIQUE,
            monthly_budget REAL NOT NULL DEFAULT 0
        )
    """)

    # Tabel transactions
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

    # Seeding awal jika master kategori kosong
    c.execute("SELECT COUNT(*) FROM categories")
    if c.fetchone()[0] == 0:
        execute_values(
            c,
            "INSERT INTO categories (tx_type, parent_category, sub_category, monthly_budget) VALUES %s ON CONFLICT DO NOTHING",
            CATEGORY_SEED
        )

    conn.commit()
    c.close()
    conn.close()

@st.cache_data
def _load_categories_df() -> pd.DataFrame:
    conn = get_supabase_conn()
    df = pd.read_sql_query(
        "SELECT id, tx_type, parent_category, sub_category, monthly_budget FROM categories ORDER BY tx_type, parent_category, sub_category",
        conn
    )
    conn.close()
    return df

def get_categories_df() -> pd.DataFrame:
    return _load_categories_df()

@st.cache_data
def get_sub_to_cat_map() -> dict:
    df = _load_categories_df()
    return dict(zip(df["sub_category"].str.lower(), df["parent_category"]))

@st.cache_data
def get_sub_budgets_map() -> dict:
    df = _load_categories_df()
    return dict(zip(df["sub_category"].str.lower(), df["monthly_budget"]))

@st.cache_data
def get_sub_options_by_type(tx_type: str) -> list:
    df = _load_categories_df()
    return sorted(df[df["tx_type"] == tx_type]["sub_category"].tolist())

@st.cache_data
def get_all_sub_options() -> list:
    df = _load_categories_df()
    return sorted(df["sub_category"].tolist())

def sub_to_cat(sub: str, tx_type: str) -> str:
    if tx_type == "Transfer":
        return "Internal Transfer"
    mapping = get_sub_to_cat_map()
    result = mapping.get(sub.lower())
    if result:
        return result
    if tx_type == "Income":
        return "Other Income"
    return "Uncategorized"

def sub_cat_options(tx_type: str) -> list:
    return get_sub_options_by_type(tx_type)

@st.cache_data
def load_all_transactions() -> pd.DataFrame:
    conn = get_supabase_conn()
    df = pd.read_sql_query(
        "SELECT id, date, description, amount, type, category, sub_category, pocket FROM transactions ORDER BY date DESC",
        conn
    )
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
                (
                    str(row["date"])[:10],
                    str(row["description"]),
                    float(row["amount"]),
                    tx_type,
                    cat,
                    sub,
                    str(row.get("pocket", ""))
                )
            )
            if c.rowcount > 0:
                inserted += 1
            else:
                skipped += 1
        except Exception:
            skipped += 1
    conn.commit()
    c.close()
    conn.close()
    return inserted, skipped

def delete_transaction(tid: int):
    conn = get_supabase_conn()
    c = conn.cursor()
    c.execute("DELETE FROM transactions WHERE id = %s", (tid,))
    conn.commit()
    c.close()
    conn.close()

def update_sub_category(tid: int, new_sub: str, tx_type: str):
    new_cat = sub_to_cat(new_sub, tx_type)
    conn = get_supabase_conn()
    c = conn.cursor()
    c.execute(
        "UPDATE transactions SET sub_category=%s, category=%s WHERE id=%s",
        (new_sub, new_cat, tid),
    )
    conn.commit()
    c.close()
    conn.close()

def save_categories(df: pd.DataFrame):
    conn = get_supabase_conn()
    c = conn.cursor()
    c.execute("DELETE FROM categories")
    for _, row in df.iterrows():
        c.execute(
            """INSERT INTO categories (tx_type, parent_category, sub_category, monthly_budget) 
               VALUES (%s, %s, %s, %s) ON CONFLICT (sub_category) DO NOTHING""",
            (
                str(row.get("tx_type", "Expense")).strip(),
                str(row.get("parent_category", "Uncategorized")).strip(),
                str(row.get("sub_category", "")).strip().lower(),
                float(row.get("monthly_budget", 0) or 0),
            ),
        )
    conn.commit()
    c.close()
    conn.close()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — UI HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def fmt_idr(v: float) -> str:
    return f"Rp {v:,.0f}".replace(",", ".")

def mom_delta(cur: float, prev: float) -> float | None:
    if prev == 0: return None
    return round((cur - prev) / prev * 100, 1)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — TAB 1: DATA INGESTION (MANUAL INPUT ONLY)
# ══════════════════════════════════════════════════════════════════════════════

def tab_upload():
    st.header("📥 Data Ingestion")
    st.markdown("Gunakan form ini jika Anda ingin mencatat transaksi tunai di luar otomasi Gmail.")

    with st.form("manual_form", clear_on_submit=True):
        c1, c2 = st.columns(2)
        with c1:
            m_type   = st.selectbox("Tipe Transaksi", ALL_TYPES)
            m_date   = st.date_input("Tanggal", value=date.today())
            m_amount = st.number_input("Jumlah (IDR)", min_value=0.0, step=10_000.0, format="%.0f")
        with c2:
            m_pocket = st.text_input("Sumber / Kantong", placeholder="e.g. Cash, BCA, OVO")
            m_desc   = st.text_input("Deskripsi", placeholder="e.g. Beli kopi swalayan")
            sub_opts = sub_cat_options(m_type)
            m_sub    = st.selectbox("Sub-Kategori", sub_opts)

        m_cat = sub_to_cat(m_sub, m_type)
        st.caption(f"Kategori otomatis: **{m_cat}**")
        submitted = st.form_submit_button("💾 Simpan Transaksi Manual", type="primary")

    if submitted:
        if m_amount <= 0:
            st.warning("Jumlah harus lebih dari 0.")
        elif not m_desc.strip():
            st.warning("Deskripsi tidak boleh kosong.")
        else:
            manual_df = pd.DataFrame([{
                "date":         m_date.strftime("%Y-%m-%d"),
                "description":  f"[MANUAL] {m_desc.strip()}",
                "amount":       m_amount,
                "type":         m_type,
                "sub_category": m_sub,
                "category":     m_cat,
                "pocket":       m_pocket.strip() or "Manual",
            }])
            ins, skp = save_transactions(manual_df)
            if ins:
                st.success(f"✅ Transaksi manual tersimpan: **{fmt_idr(m_amount)}**")
            st.cache_data.clear()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — TAB 2: FINANCIAL DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def tab_dashboard(sel_year: int, sel_months: list, df_all: pd.DataFrame):
    st.header("📊 Financial Dashboard")

    if df_all.empty:
        st.info("Belum ada data di database. Pastikan robot Gmail atau Input Manual sudah berjalan.")
        return

    # Filter data bersih (Eksklusi transfer internal & data yang masih 'uncategorized')
    df_all_clean = df_all[(df_all["type"] != "Transfer") & (df_all["sub_category"] != "uncategorized")].copy()

    sub_monthly, sub_annual = st.tabs(["📅 Monthly Tactical", "🏆 Annual Strategic & Insights"])

    with sub_monthly:
        df = df_all_clean[(df_all_clean["year"] == sel_year) & (df_all_clean["month"].isin(sel_months))].copy()

        if df.empty:
            st.warning("Tidak ada data bersih (terkategorisasi) untuk filter bulan/tahun ini.")
        else:
            inc_df = df[df["type"] == "Income"]
            exp_df = df[df["type"] == "Expense"]
            ti, te = inc_df["amount"].sum(), exp_df["amount"].sum()
            net    = ti - te
            burn   = (te / ti * 100) if ti > 0 else 0

            # KPI Cards
            st.subheader("📈 Executive Summary")
            k1, k2, k3, k4 = st.columns(4)
            k1.metric("💰 Total Pemasukan",   fmt_idr(ti))
            k2.metric("💸 Total Pengeluaran", fmt_idr(te))
            k3.metric("🏦 Net Tabungan",      fmt_idr(net))
            k4.metric("🔥 Burn Rate",         f"{burn:.1f}%")

            st.divider()

            # Progress Bar Budget
            st.subheader("🎯 Budget vs. Aktual (Total)")
            budget_total = MONTHLY_BUDGET * max(len(sel_months), 1)
            ratio = min(te / budget_total, 1.0) if budget_total > 0 else 0
            cb, cp = st.columns([4, 1])
            cb.progress(ratio)
            cp.write(f"{ratio * 100:.1f}%")

            st.divider()

            # Grafik Budget vs Aktual per Sub-Kategori
            st.subheader("🎯 Budget vs. Aktual per Sub-Kategori")
            sub_budgets_map = get_sub_budgets_map()
            actual_by_sub   = exp_df.groupby("sub_category")["amount"].sum()
            num_months       = max(len(sel_months), 1)

            budget_rows = []
            for sub, budget_monthly in sub_budgets_map.items():
                actual  = actual_by_sub.get(sub, 0.0)
                budget  = budget_monthly * num_months
                if actual == 0 and budget == 0: continue
                budget_rows.append({
                    "Sub-Kategori": sub,
                    "Aktual":       actual,
                    "Budget":       budget
                })

            bdf = pd.DataFrame(budget_rows) if budget_rows else pd.DataFrame()
            if not bdf.empty:
                bdf = bdf[bdf["Aktual"] > 0].sort_values("Aktual", ascending=False)
                fig_budget = go.Figure()
                fig_budget.add_trace(go.Bar(name="Aktual", x=bdf["Sub-Kategori"], y=bdf["Aktual"], marker_color="#e74c3c"))
                fig_budget.add_trace(go.Bar(name="Budget", x=bdf["Sub-Kategori"], y=bdf["Budget"], marker_color="#95a5a6", opacity=0.6))
                fig_budget.update_layout(barmode="group", plot_bgcolor="rgba(0,0,0,0)")
                st.plotly_chart(fig_budget, use_container_width=True)

            st.divider()

            # Donut Chart Kategori Utama
            c1, c2 = st.columns(2)
            with c1:
                cat_e = exp_df.groupby("category")["amount"].sum().reset_index()
                st.plotly_chart(px.pie(cat_e, values="amount", names="category", title="Pengeluaran per Kategori Utama", hole=0.4), use_container_width=True)
            with c2:
                sub_tbl = exp_df.groupby(["category", "sub_category"])["amount"].sum().reset_index()
                sub_tbl["Jumlah"] = sub_tbl["amount"].apply(fmt_idr)
                st.markdown("**Tabel Distribusi Sub-Kategori**")
                st.dataframe(sub_tbl[["category", "sub_category", "Jumlah"]].sort_values("category"), use_container_width=True, height=280)

    with sub_annual:
        df_year = df_all_clean[df_all_clean["year"] == sel_year].copy()
        if df_year.empty:
            st.warning(f"Tidak ada data tahun {sel_year} yang terkategorisasi.")
        else:
            df_year["year_month"] = df_year["date"].dt.to_period("M").astype(str)
            
            # Cumulative Wealth Trajectory
            st.subheader("📈 Cumulative Wealth Trajectory")
            daily_net = df_year.groupby("date").apply(lambda g: g[g["type"] == "Income"]["amount"].sum() - g[g["type"] == "Expense"]["amount"].sum()).reset_index().rename(columns={0: "net"}).sort_values("date")
            daily_net["cumulative"] = daily_net["net"].cumsum()

            fig_cum = go.Figure()
            fig_cum.add_trace(go.Scatter(x=daily_net["date"], y=daily_net["cumulative"], mode="lines", fill="tozeroy", line=dict(color="#2980b9", width=2), name="Kumulatif Net"))
            fig_cum.update_layout(plot_bgcolor="rgba(0,0,0,0)")
            st.plotly_chart(fig_cum, use_container_width=True)

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 6 — TAB 3: EXPORT
# ══════════════════════════════════════════════════════════════════════════════

def tab_export():
    st.header("📤 Export Data")
    df = load_all_transactions()
    if df.empty:
        st.info("Belum ada data untuk diexport.")
        return

    st.metric("Total Transaksi di Database Cloud", len(df))
    
    buf = io.BytesIO()
    with pd.ExcelWriter(buf, engine="openpyxl") as w:
        ex = df.copy()
        ex["date"] = ex["date"].dt.strftime("%Y-%m-%d")
        ex.to_excel(w, index=False, sheet_name="Transaksi Lengkap")

    buf.seek(0)
    st.download_button(
        "⬇️ Download Excel",
        data=buf.getvalue(),
        file_name=f"finance_supabase_{datetime.now().strftime('%Y%m%d')}.xlsx",
        mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        type="primary"
    )

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 7 — TAB 4: KELOLA DATA & VALIDASI ANTRIAN (OPTIMIZED FOR MOBILE HP)
# ══════════════════════════════════════════════════════════════════════════════

def tab_manage():
    st.header("⚙️ Kelola Data & Validasi")

    sub_tx, sub_master = st.tabs(["📋 Antrean Data Mentah & Edit", "⚙️ Master Data Kategori"])

    with sub_tx:
        df = load_all_transactions()
        if df.empty:
            st.info("Belum ada transaksi historis.")
        else:
            dd = df.copy()
            dd["date"] = dd["date"].dt.strftime("%Y-%m-%d")

            # Badges filter untuk mempermudah melihat data unread mentah dari HP
            st.subheader("📋 Verifikasi Transaksi Masuk")
            
            # Hitung jumlah antrean data mentah dari Gmail
            antrean_count = len(df[df["sub_category"] == "uncategorized"])
            if antrean_count > 0:
                st.warning(f"⚠️ Ada **{antrean_count} transaksi mentah** baru dari Gmail yang butuh kategori.")
            else:
                st.success("✅ Semua transaksi sudah bersih dan terkategorisasi!")

            st.markdown("---")
            st.markdown("**Form Validasi Kategori:**")

            sel_id = st.selectbox(
                "Pilih ID Transaksi untuk diklasifikasi/diubah:",
                options=dd["id"].tolist(),
                format_func=lambda i: (
                    f"ID {i} | "
                    + dd[dd["id"] == i]["date"].values[0] + " | "
                    + dd[dd["id"] == i]["description"].values[0][:35] + " | "
                    + fmt_idr(dd[dd["id"] == i]["amount"].values[0])
                    + (" ⚠️ MENTAH" if dd[dd["id"] == i]["sub_category"].values[0] == "uncategorized" else " ✅ BERSIH")
                ),
                key="select_validation_id"
            )

            sel_row  = dd[dd["id"] == sel_id].iloc[0]
            sel_type = sel_row["type"]
            cur_sub  = sel_row["sub_category"]

            opts       = sub_cat_options(sel_type)
            default_ix = opts.index(cur_sub) if cur_sub in opts else 0
            
            new_sub    = st.selectbox("Pilih Sub-Kategori yang tepat via HP:", opts, index=default_ix, key="manage_sub")
            new_cat    = sub_to_cat(new_sub, sel_type)

            col_a, col_b = st.columns(2)
            with col_a:
                if st.button("✅ Sahkan & Update Kategori", type="primary", use_container_width=True):
                    update_sub_category(sel_id, new_sub, sel_type)
                    st.success(f"Sukses mengesahkan ID {sel_id} ke sub-kategori: **{new_sub}**!")
                    st.cache_data.clear()
                    st.rerun()
            with col_b:
                if st.button("🗑️ Hapus Baris Transaksi", type="secondary", use_container_width=True):
                    delete_transaction(sel_id)
                    st.info(f"ID {sel_id} berhasil dihapus.")
                    st.cache_data.clear()
                    st.rerun()

            st.divider()
            st.markdown("**Daftar Seluruh Log Transaksi di Database:**")
            disp = dd[["id", "date", "description", "amount", "sub_category", "pocket"]].copy()
            disp["amount"] = disp["amount"].apply(fmt_idr)
            st.dataframe(disp, use_container_width=True, height=350)

    with sub_master:
        st.subheader("⚙️ Master Data Kategori")
        cat_df = get_categories_df().copy()
        cat_edit_df = cat_df.drop(columns=["id"]).copy()

        edited_cats = st.data_editor(
            cat_edit_df,
            column_config={
                "tx_type": st.column_config.SelectboxColumn("Tipe Transaksi", options=ALL_TYPES, required=True),
                "parent_category": st.column_config.TextColumn("Kategori Utama"),
                "sub_category": st.column_config.TextColumn("Sub-Kategori (unik)"),
                "monthly_budget": st.column_config.NumberColumn("Budget Bulanan", format="Rp %.0f")
            },
            use_container_width=True,
            num_rows="dynamic",
            key="master_data_editor",
            height=400
        )

        if st.button("💾 Simpan Perubahan Master Data", type="primary"):
            save_categories(edited_cats)
            st.cache_data.clear()
            st.success("Master data kategori berhasil diperbarui!")
            st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — MAIN ENTRY POINT
# ══════════════════════════════════════════════════════════════════════════════

def main():
    st.set_page_config(
        page_title="Finance Dashboard v5.1",
        page_icon="💰",
        layout="wide",
        initial_sidebar_state="collapsed", # Menghemat space layar jika dibuka di HP
    )
    init_db()

    st.sidebar.title("💰 Holistic Wealth")
    st.sidebar.markdown("*The CFO Console Cloud v5.1*")
    st.sidebar.markdown("---")

    nav = st.sidebar.radio("Navigasi", ["📊 Dashboard", "⚙️ Kelola Data & Validasi", "📥 Ingestion Manual", "📤 Export"], label_visibility="collapsed")

    df_all = load_all_transactions()
    sel_year = datetime.now().year
    sel_months = list(range(1, 13))

    if not df_all.empty:
        df_all["year"]  = df_all["date"].dt.year
        df_all["month"] = df_all["date"].dt.month

        df_ie = df_all[(df_all["type"] != "Transfer") & (df_all["sub_category"] != "uncategorized")]
        
        st.sidebar.markdown("---")
        st.sidebar.subheader("🔽 Filter Dashboard")
        sel_year = st.sidebar.selectbox(
            "Tahun Evaluation",
            sorted(df_ie["year"].unique(), reverse=True) if not df_ie.empty else [datetime.now().year],
            key="sb_year"
        )
        
        m_avail = sorted(df_ie[df_ie["year"] == sel_year]["month"].unique()) if not df_ie.empty else list(range(1, 13))
        m_labels = {m: datetime(2000, m, 1).strftime("%B") for m in m_avail}
        sel_months = st.sidebar.multiselect("Bulan", m_avail, default=m_avail, format_func=lambda m: m_labels.get(m, str(m)), key="sb_months")

    # Tombol shortcut navigasi atas khusus untuk kenyamanan layar HP Anda
    st.markdown("### 📲 CFO Console Quick Navigation")
    c_nav1, c_nav2, c_nav3 = st.columns(3)
    if c_nav1.button("📊 Dashboard View", use_container_width=True): st.session_state.navigation = "📊 Dashboard"
    if c_nav2.button("⚙️ Validasi Antrean", use_container_width=True): st.session_state.navigation = "⚙️ Kelola Data & Validasi"
    if c_nav3.button("📥 Input Manual", use_container_width=True): st.session_state.navigation = "📥 Ingestion Manual"

    # Handle state navigasi quick button
    if "navigation" in st.session_state:
        nav = st.session_state.navigation

    st.markdown("---")

    # Routing Halaman
    if nav == "📥 Ingestion Manual": tab_upload()
    elif nav == "📊 Dashboard": tab_dashboard(sel_year, sel_months, df_all)
    elif nav == "📤 Export": tab_export()
    elif nav == "⚙️ Kelola Data & Validasi": tab_manage()

if __name__ == "__main__":
    main()