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

# Warna per parent category untuk chart harian
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
    """Konversi hex color ke rgba string yang valid untuk Plotly."""
    h = hex_color.lstrip("#")
    r, g, b = int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16)
    return f"rgba({r},{g},{b},{alpha})"

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 2 — DATABASE LAYER (OPTIMIZED)
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
    conn = _conn()
    rows = []
    for _, row in df.iterrows():
        sub = str(row.get("sub_category", "uncategorized")).strip()
        tx_type = str(row["type"])
        rows.append((
            str(row["date"])[:10],
            str(row["description"]),
            float(row["amount"]),
            tx_type,
            sub_to_cat(sub, tx_type),
            sub,
            str(row.get("pocket", "")),
        ))
    if not rows:
        return 0, 0
    with conn.cursor() as c:
        execute_values(
            c,
            """INSERT INTO transactions
               (date, description, amount, type, category, sub_category, pocket)
               VALUES %s ON CONFLICT (date, description, amount) DO NOTHING""",
            rows,
        )
        inserted = c.rowcount
    conn.commit()
    load_all_transactions.clear()
    return inserted, len(rows) - inserted


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


# ── Master Data: Categories ───────────────────────────────────────────────────

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

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 3 — PDF PARSER ENGINE
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
                                "description": match.group(2)[:40], "amount": amount, "type": tx_type,
                                "pocket": "Sinarmas", "sub_category": "uncategorized", "category": "Uncategorized",
                            })
                    elif bank_type == "Jenius CC":
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+\d{2}\s[a-zA-Z]{3}\s\d{4}\s+(.+?)\s+([\d\,\.]+)(?:\sCR)?$', line)
                        if match:
                            amount = float(match.group(3).replace(',', ''))
                            tx_type = "Income" if "CR" in line or "Pembayaran" in line else "Expense"
                            transactions.append({
                                "date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"),
                                "description": match.group(2)[:40], "amount": amount, "type": tx_type,
                                "pocket": "Jenius CC", "sub_category": "uncategorized", "category": "Uncategorized",
                            })
                    elif bank_type == "Bank Jago":
                        match = re.search(r'(\d{2}\s[a-zA-Z]{3}\s\d{4})\s+\d{2}\.\d{2}\s+(.+?)\s+([\-\+])([\d\.]+)', line)
                        if match:
                            amount = float(match.group(4).replace('.', ''))
                            tx_type = "Income" if match.group(3) == "+" else "Expense"
                            transactions.append({
                                "date": datetime.strptime(match.group(1), "%d %b %Y").strftime("%Y-%m-%d"),
                                "description": match.group(2)[:40], "amount": amount, "type": tx_type,
                                "pocket": "Bank Jago", "sub_category": "uncategorized", "category": "Uncategorized",
                            })
    except Exception as e:
        return None, str(e)
    return pd.DataFrame(transactions), None

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 4 — HELPERS
# ══════════════════════════════════════════════════════════════════════════════

def fmt_idr(v: float) -> str:
    return f"Rp {v:,.0f}".replace(",", ".")


def plotly_base() -> dict:
    return dict(
        plot_bgcolor="rgba(0,0,0,0)",
        paper_bgcolor="rgba(0,0,0,0)",
        margin=dict(l=0, r=0, t=30, b=0),
        hovermode="x unified",
        legend=dict(orientation="h", yanchor="bottom", y=1.02, xanchor="right", x=1),
    )

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 5 — UI: DASHBOARD
# ══════════════════════════════════════════════════════════════════════════════

def tab_dashboard(df_all: pd.DataFrame):
    st.title("📊 Financial Dashboard")
    if df_all.empty:
        st.info("Belum ada data. Silakan upload PDF Statement atau input manual.")
        return

    df_clean = df_all[
        (df_all["type"] != "Transfer") &
        (df_all["sub_category"] != "uncategorized")
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
            sel_months   = col3.multiselect(
                "Bulan", avail_months, default=avail_months,
                format_func=lambda m: datetime(2000, m, 1).strftime("%B"),
            )
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
    c1.metric("💰 Pemasukan",  fmt_idr(ti))
    c2.metric("💸 Pengeluaran", fmt_idr(te))
    c3.metric("🏦 Net Income", fmt_idr(ti - te))
    c4.metric("🔥 Burn Rate",  f"{(te / ti * 100) if ti > 0 else 0:.1f}%")
    st.divider()

    st.markdown("### 📉 Tren Pengeluaran vs Pemasukan")
    fig_line = go.Figure()
    for grp, color, label in [
        (exp_df, "#e74c3c", "Pengeluaran"),
        (inc_df, "#2ecc71", "Pemasukan"),
    ]:
        d = grp.groupby(time_group)["amount"].sum().reset_index()
        fig_line.add_trace(go.Scatter(
            x=d[time_group], y=d["amount"],
            mode="lines+markers", name=label,
            line=dict(color=color, width=3),
        ))
    n_months = len(sel_months) if view_mode == "Bulanan" else 1
    fig_line.add_hline(
        y=MONTHLY_BUDGET * n_months,
        line_dash="dash", line_color="#f39c12", line_width=2,
        annotation_text=f"Budget ({fmt_idr(MONTHLY_BUDGET * n_months)})",
        annotation_position="top right",
    )
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
# SECTION 6 — UI: BUDGET VS ACTUAL (FIXED + ENHANCED)
# ══════════════════════════════════════════════════════════════════════════════

def tab_budget_vs_actual(df_all: pd.DataFrame):
    st.title("🎯 Budget vs Actual")

    if df_all.empty:
        st.info("Belum ada data transaksi.")
        return

    df_exp = df_all[
        (df_all["type"] == "Expense") &
        (df_all["sub_category"] != "uncategorized")
    ].copy()
    df_exp["year"]  = df_exp["date"].dt.year
    df_exp["month"] = df_exp["date"].dt.month
    df_exp["day"]   = df_exp["date"].dt.day

    # ── Filter Tahun + Bulan ──────────────────────────────────────────────────
    with st.container(border=True):
        col1, col2 = st.columns([1, 3])
        avail_years = sorted(df_exp["year"].unique(), reverse=True) if not df_exp.empty else [datetime.now().year]
        sel_year    = col1.selectbox("Filter Tahun", avail_years)
        df_year     = df_exp[df_exp["year"] == sel_year]
        avail_months = sorted(df_year["month"].unique())
        sel_months   = col2.multiselect(
            "Filter Bulan", avail_months, default=avail_months,
            format_func=lambda m: datetime(2000, m, 1).strftime("%B"),
        )

    if not sel_months:
        st.warning("Pilih minimal satu bulan.")
        return

    df_f     = df_year[df_year["month"].isin(sel_months)]
    n_months = len(sel_months)

    # Ambil budget dari DB (real-time, bukan hardcode)
    cat_df  = load_categories_df()
    bud_map = dict(zip(cat_df["sub_category"], cat_df["monthly_budget"]))
    total_budget_from_db = cat_df[cat_df["tx_type"] == "Expense"]["monthly_budget"].sum() * n_months

    total_actual = df_f["amount"].sum()
    burn_rate    = (total_actual / total_budget_from_db * 100) if total_budget_from_db > 0 else 0
    selisih      = total_budget_from_db - total_actual

    # ── KPI ──────────────────────────────────────────────────────────────────
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("🎯 Total Budget",  fmt_idr(total_budget_from_db), f"{n_months} bulan")
    k2.metric("💸 Total Actual",  fmt_idr(total_actual))
    k3.metric(
        "📊 Selisih",
        fmt_idr(abs(selisih)),
        delta=f"{'Under' if selisih >= 0 else 'Over'} budget",
        delta_color="normal" if selisih >= 0 else "inverse",
    )
    k4.metric("🔥 Burn Rate", f"{burn_rate:.1f}%")
    st.divider()

    # ── Actual vs Budget per Sub-Kategori ────────────────────────────────────
    st.markdown("### 📊 Actual vs Budget per Sub-Kategori")

    actual_by_sub = df_f.groupby("sub_category")["amount"].sum().reset_index()
    actual_by_sub.columns = ["sub_category", "actual"]
    actual_by_sub["budget"] = actual_by_sub["sub_category"].map(bud_map).fillna(0) * n_months
    actual_by_sub["status"] = actual_by_sub.apply(
        lambda r: "Over Budget 🔴" if r["actual"] > r["budget"] else "Under Budget 🟢", axis=1
    )
    actual_by_sub = actual_by_sub.sort_values("actual", ascending=True)

    fig_bva = go.Figure()
    fig_bva.add_trace(go.Bar(
        y=actual_by_sub["sub_category"],
        x=actual_by_sub["actual"],
        name="Actual",
        orientation="h",
        marker_color=[
            "#E24B4A" if row["actual"] > row["budget"] else "#639922"
            for _, row in actual_by_sub.iterrows()
        ],
    ))
    fig_bva.add_trace(go.Scatter(
        y=actual_by_sub["sub_category"],
        x=actual_by_sub["budget"],
        name="Budget",
        mode="markers",
        marker=dict(symbol="line-ns", size=14, color="#f39c12",
                    line=dict(width=2, color="#f39c12")),
    ))
    fig_bva.update_layout(
        **plotly_base(),
        height=max(350, len(actual_by_sub) * 32),
        xaxis_title="IDR",
        yaxis_title="",
    )
    st.plotly_chart(fig_bva, use_container_width=True)

    # ── Progress Bar per Parent Category ─────────────────────────────────────
    st.markdown("### 📁 Realisasi per Kategori Induk")

    parent_budget = (
        cat_df[cat_df["tx_type"] == "Expense"]
        .groupby("parent_category")["monthly_budget"].sum() * n_months
    ).reset_index()
    parent_budget.columns = ["category", "budget"]

    actual_by_cat = df_f.groupby("category")["amount"].sum().reset_index()
    actual_by_cat.columns = ["category", "actual"]

    merged = parent_budget.merge(actual_by_cat, on="category", how="left").fillna(0)
    merged = merged[merged["budget"] > 0].sort_values("budget", ascending=False)

    for _, row in merged.iterrows():
        pct   = (row["actual"] / row["budget"] * 100) if row["budget"] > 0 else 0
        over  = row["actual"] > row["budget"]
        label = "🔴" if over else ("🟡" if pct > 85 else "🟢")
        c_a, c_b = st.columns([3, 1])
        c_a.markdown(f"**{row['category']}** {label}")
        c_b.markdown(f"`{fmt_idr(row['actual'])} / {fmt_idr(row['budget'])}` — **{min(pct,100):.1f}%**")
        st.progress(min(pct / 100, 1.0))

    st.divider()

    # ── Pengeluaran Harian — Total ────────────────────────────────────────────
    st.markdown("### 📅 Pengeluaran Harian — Total")

    import calendar
    total_days = sum(calendar.monthrange(sel_year, m)[1] for m in sel_months)
    daily_budget_line = total_budget_from_db / total_days if total_days > 0 else MONTHLY_BUDGET / 30

    all_days    = pd.DataFrame({"day": range(1, 32)})
    daily_total = df_f.groupby("day")["amount"].sum().reset_index()
    daily_total = all_days.merge(daily_total, on="day", how="left").fillna(0)

    fig_daily = go.Figure()
    fig_daily.add_trace(go.Scatter(
        x=daily_total["day"], y=daily_total["amount"],
        mode="lines+markers", name="Actual Harian",
        line=dict(color="#E24B4A", width=2),
        marker=dict(size=5),
        fill="tozeroy",
        fillcolor=hex_to_rgba("#E24B4A", 0.08),
    ))
    fig_daily.add_hline(
        y=daily_budget_line,
        line_dash="dash", line_color="#f39c12", line_width=2,
        annotation_text=f"Budget/hari ({fmt_idr(daily_budget_line)})",
        annotation_position="top right",
    )
    fig_daily.update_layout(
        **plotly_base(),
        height=320,
        xaxis=dict(title="Tanggal", tickmode="linear", tick0=1, dtick=5),
        yaxis=dict(title="IDR"),
    )
    st.plotly_chart(fig_daily, use_container_width=True)

    # ── Pengeluaran Harian — Per Kategori (FIXED fillcolor) ───────────────────
    st.markdown("### 🗂️ Pengeluaran Harian per Kategori")

    parent_cats = sorted(df_f["category"].unique().tolist())
    sel_cats    = st.multiselect(
        "Pilih Kategori:", parent_cats, default=parent_cats,
    )

    if sel_cats:
        df_cat_day = (
            df_f[df_f["category"].isin(sel_cats)]
            .groupby(["day", "category"])["amount"]
            .sum().reset_index()
        )
        fig_cat = go.Figure()
        for cat in sel_cats:
            cat_data = all_days.merge(
                df_cat_day[df_cat_day["category"] == cat],
                on="day", how="left",
            ).fillna(0)
            color      = CATEGORY_COLORS.get(cat, "#888780")
            fill_color = hex_to_rgba(color, 0.15)          # ← FIXED: pakai hex_to_rgba()
            fig_cat.add_trace(go.Scatter(
                x=cat_data["day"],
                y=cat_data["amount"],
                name=cat,
                mode="lines",
                line=dict(color=color, width=2),
                fill="tonexty",
                fillcolor=fill_color,
                stackgroup="one",
            ))
        fig_cat.add_hline(
            y=daily_budget_line,
            line_dash="dash", line_color="#f39c12", line_width=2,
            annotation_text=f"Budget/hari ({fmt_idr(daily_budget_line)})",
            annotation_position="top right",
        )
        fig_cat.update_layout(
            **plotly_base(),
            height=380,
            xaxis=dict(title="Tanggal", tickmode="linear", tick0=1, dtick=5),
            yaxis=dict(title="IDR"),
        )
        st.plotly_chart(fig_cat, use_container_width=True)
    else:
        st.info("Pilih minimal satu kategori.")

    st.divider()

    # ── Tabel Ringkasan ───────────────────────────────────────────────────────
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
# SECTION 7 — UI: MASTER DATA MANAGEMENT (NEW)
# ══════════════════════════════════════════════════════════════════════════════

def tab_master_data():
    st.title("🗂️ Master Data Management")
    st.caption("Kelola kategori, sub-kategori, dan budget bulanan per sub-kategori.")

    cat_df = load_categories_df()

    tab_exp, tab_inc, tab_trans = st.tabs([
        "💸 Expense Categories", "💰 Income Categories", "🔄 Transfer"
    ])

    # ── EXPENSE ───────────────────────────────────────────────────────────────
    with tab_exp:
        exp_df = cat_df[cat_df["tx_type"] == "Expense"].copy()

        st.markdown("#### ➕ Tambah / Edit Sub-Kategori Expense")
        with st.form("form_add_exp", clear_on_submit=True):
            c1, c2, c3 = st.columns(3)
            new_parent = c1.text_input("Parent Category", placeholder="e.g. Health & Wellness")
            new_sub    = c2.text_input("Sub-Category", placeholder="e.g. vitamins")
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

        # Tampilkan per parent category
        for parent in sorted(exp_df["parent_category"].unique()):
            grp = exp_df[exp_df["parent_category"] == parent].reset_index(drop=True)
            total_bud = grp["monthly_budget"].sum()
            with st.expander(f"**{parent}** — Budget total: {fmt_idr(total_bud)}/bln", expanded=True):
                for _, row in grp.iterrows():
                    col_a, col_b, col_c, col_d = st.columns([3, 2, 1, 1])
                    col_a.markdown(f"**{row['sub_category']}**")

                    # Edit budget inline
                    new_bud = col_b.number_input(
                        "Budget/bln",
                        value=float(row["monthly_budget"]),
                        min_value=0.0,
                        step=50_000.0,
                        key=f"bud_{row['sub_category']}",
                        label_visibility="collapsed",
                    )
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

        # ── Summary budget total ──────────────────────────────────────────────
        total_all = exp_df["monthly_budget"].sum()
        st.markdown(f"**💡 Total Budget Expense per bulan: {fmt_idr(total_all)}**")

        # ── Bulk budget update via tabel editable ─────────────────────────────
        st.markdown("#### ✏️ Edit Budget Massal (Tabel)")
        edit_df = exp_df[["parent_category", "sub_category", "monthly_budget"]].copy()
        edit_df.columns = ["Parent Category", "Sub-Kategori", "Budget Bulanan (IDR)"]
        edited = st.data_editor(
            edit_df,
            use_container_width=True,
            hide_index=True,
            column_config={
                "Budget Bulanan (IDR)": st.column_config.NumberColumn(
                    "Budget Bulanan (IDR)", min_value=0, step=50000, format="Rp %d"
                ),
                "Parent Category": st.column_config.TextColumn(disabled=True),
                "Sub-Kategori":    st.column_config.TextColumn(disabled=True),
            },
        )
        if st.button("💾 Simpan Semua Perubahan Budget", type="primary"):
            for _, row in edited.iterrows():
                update_budget(row["Sub-Kategori"], float(row["Budget Bulanan (IDR)"]))
            st.success("✅ Semua budget berhasil diperbarui!")
            load_categories_df.clear()
            st.rerun()

    # ── INCOME ────────────────────────────────────────────────────────────────
    with tab_inc:
        inc_df = cat_df[cat_df["tx_type"] == "Income"].copy()

        st.markdown("#### ➕ Tambah Sub-Kategori Income")
        with st.form("form_add_inc", clear_on_submit=True):
            c1, c2 = st.columns(2)
            new_parent_i = c1.text_input("Parent Category", placeholder="e.g. Freelance")
            new_sub_i    = c2.text_input("Sub-Category", placeholder="e.g. project fee")
            if st.form_submit_button("💾 Simpan", type="primary"):
                if new_parent_i and new_sub_i:
                    upsert_category("Income", new_parent_i, new_sub_i, 0)
                    st.success(f"✅ '{new_sub_i}' berhasil disimpan!")
                    st.rerun()
                else:
                    st.warning("Semua field wajib diisi.")

        st.divider()
        st.markdown("#### 📋 Daftar Sub-Kategori Income")
        for _, row in inc_df.iterrows():
            c1, c2 = st.columns([4, 1])
            c1.markdown(f"**{row['sub_category']}** — _{row['parent_category']}_")
            if c2.button("🗑️ Hapus", key=f"del_inc_{row['sub_category']}"):
                delete_category(row["sub_category"])
                st.rerun()

    # ── TRANSFER ──────────────────────────────────────────────────────────────
    with tab_trans:
        tra_df = cat_df[cat_df["tx_type"] == "Transfer"].copy()
        st.markdown("#### 📋 Daftar Sub-Kategori Transfer")
        st.dataframe(
            tra_df[["parent_category", "sub_category"]].rename(
                columns={"parent_category": "Parent", "sub_category": "Sub-Kategori"}
            ),
            use_container_width=True, hide_index=True,
        )
        with st.form("form_add_tra", clear_on_submit=True):
            new_sub_t = st.text_input("Tambah Sub-Category Transfer", placeholder="e.g. tabungan bersama")
            if st.form_submit_button("💾 Simpan"):
                if new_sub_t:
                    upsert_category("Transfer", "Internal Transfer", new_sub_t, 0)
                    st.success(f"✅ '{new_sub_t}' ditambahkan!")
                    st.rerun()

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 8 — UI: INGESTION DATA
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
                    "pocket": m_pocket,
                }])
                save_transactions(manual_df)
                st.success("Tersimpan!")

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 9 — UI: VALIDASI
# ══════════════════════════════════════════════════════════════════════════════

def tab_validation(df_all: pd.DataFrame):
    st.title("⚙️ Kelola & Validasi Data")

    uncat_df = df_all[df_all["sub_category"] == "uncategorized"].copy()
    if not uncat_df.empty:
        st.warning(f"⚠️ Terdapat **{len(uncat_df)}** transaksi mentah yang butuh disahkan!")
        sel_id  = st.selectbox(
            "Pilih Transaksi Mentah:",
            uncat_df["id"].tolist(),
            format_func=lambda i: (
                f"ID {i} | {str(uncat_df[uncat_df['id']==i]['date'].values[0])[:10]} | "
                f"{fmt_idr(uncat_df[uncat_df['id']==i]['amount'].values[0])} | "
                f"{uncat_df[uncat_df['id']==i]['description'].values[0][:30]}"
            ),
        )
        sel_row = uncat_df[uncat_df["id"] == sel_id].iloc[0]
        df_cat  = load_categories_df()
        opts    = sorted(df_cat[df_cat["tx_type"] == sel_row["type"]]["sub_category"].tolist())
        new_sub = st.selectbox("Sahkan ke Sub-Kategori:", opts)
        col1, col2 = st.columns(2)
        if col1.button("✅ Sahkan Transaksi", type="primary", use_container_width=True):
            update_sub_category(int(sel_id), new_sub, sel_row["type"])
            st.success("Tersahkan!")
            st.rerun()
        if col2.button("🗑️ Hapus Transaksi", type="secondary", use_container_width=True):
            delete_transaction(int(sel_id))
            st.rerun()
    else:
        st.success("🎉 Seluruh data transaksi sudah bersih dan terkategorisasi!")

    st.divider()
    st.markdown("### 📋 Database Histori Log")
    st.dataframe(
        df_all[["date", "description", "amount", "type", "sub_category", "pocket"]],
        use_container_width=True, height=300,
    )

# ══════════════════════════════════════════════════════════════════════════════
# SECTION 10 — MAIN ENTRY POINT
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

    nav_selection = st.sidebar.radio(
        "Navigasi Utama",
        ["📊 Dashboard", "🎯 Budget vs Actual", "🗂️ Master Data", "⚙️ Validasi Antrean", "📥 Ingestion Data"],
        key="current_nav",
    )

    df_all = load_all_transactions()

    if nav_selection == "📊 Dashboard":
        tab_dashboard(df_all)
    elif nav_selection == "🎯 Budget vs Actual":
        tab_budget_vs_actual(df_all)
    elif nav_selection == "🗂️ Master Data":
        tab_master_data()
    elif nav_selection == "📥 Ingestion Data":
        tab_ingestion()
    elif nav_selection == "⚙️ Validasi Antrean":
        tab_validation(df_all)


if __name__ == "__main__":
    main()
