"""№5. Отчёт → глубина хранения данных."""

from __future__ import annotations

import plotly.express as px
import streamlit as st

from _shared import ACCENT, download, page_setup, query, rc_scope, rc_selector, search_box, show_table

page_setup("№5. Отчёт → глубина хранения", "5️⃣")
st.caption(
    "Глубина задана на таблицу. Глубина отчёта — **максимум** по его таблицам: "
    "отчёт показывает столько дней, сколько хранит самая «долгая» его таблица."
)

network, plant = rc_selector()
df = rc_scope(query("SELECT * FROM v_rc_report_retention"), network, plant)

if not df["retention_days"].notna().any():
    st.info(
        "Глубина хранения не загружена. Добавьте в файл размеров таблиц колонку "
        "**«Глубина хранения»** (в днях) — витрина заполнится автоматически, "
        "править конфиг не нужно."
    )
else:
    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Более 45 дней", int((df["retention_band"] == "Более 45 дней").sum()))
    c2.metric("От 31 до 45 дней", int((df["retention_band"] == "От 31 до 45 дней").sum()))
    c3.metric("Медиана, дней", f"{df['retention_days'].median():.0f}")
    c4.metric("Без глубины", int(df["retention_days"].isna().sum()))

    bands = ["До 30 дней", "От 31 до 45 дней", "Более 45 дней", "Не задана"]
    counts = df.groupby("retention_band", as_index=False).agg(report_count=("report_name", "count"))
    counts["order"] = counts["retention_band"].apply(
        lambda b: bands.index(b) if b in bands else len(bands))
    counts = counts.sort_values("order")
    fig = px.bar(
        counts, x="retention_band", y="report_count",
        labels={"retention_band": "", "report_count": "Отчётов"},
        color_discrete_sequence=[ACCENT],
    )
    fig.update_layout(height=320, margin=dict(l=0, r=0, t=10, b=0), bargap=0.4)
    st.plotly_chart(fig, use_container_width=True)
    st.caption(
        "Отчёты с глубиной более 45 дней — первые кандидаты на сокращение срока "
        "хранения, если такая глубина не нужна бизнесу."
    )

view = search_box(df, ["report_name", "catalog_path"],
                  "Поиск по наименованию отчёта", key="s_rc5")
shown = show_table(
    view.sort_values("retention_days", ascending=False),
    {
        "retention_band": "Группа по глубине",
        "retention_days": st.column_config.NumberColumn("Глубина, дней"),
        "retention_days_min": st.column_config.NumberColumn("Минимум, дней"),
        "tables_with_retention": "Таблиц с глубиной",
    },
)
download(shown, "rc_5_report_retention.csv")
