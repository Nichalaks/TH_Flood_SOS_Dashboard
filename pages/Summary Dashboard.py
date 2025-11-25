import streamlit as st
import pandas as pd
import requests
import json
import altair as alt

st.title("📊 Summary Dashboard")



# -----------------------------
# 1) Load data + parse location
# -----------------------------
@st.cache_data(ttl=600)
def load_data():
    url = "https://jitasa.care/ajax/v1/sos"
    raw = requests.get(url, timeout=60).json()

    if isinstance(raw, dict) and "data" in raw:
        records = raw["data"]
    else:
        records = raw

    df = pd.DataFrame(records)

    # datetime + ตัด timezone ถ้ามี
    for col in ["created_at", "updated_at", "expired_at", "date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
            if pd.api.types.is_datetime64tz_dtype(df[col]):
                df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)

    # parse location เฉพาะ field ที่ต้องใช้
    if "location" in df.columns:

        def parse_location(loc_obj):
            out = {
                "province": None,
                "district": None,
                "subdistrict": None,
                "status_text": None,
                "type_name": None,
            }

            if isinstance(loc_obj, str):
                try:
                    loc = json.loads(loc_obj)
                except Exception:
                    return pd.Series(out)
            elif isinstance(loc_obj, dict):
                loc = loc_obj
            else:
                return pd.Series(out)

            props = loc.get("properties", {}) or {}

            out["province"] = (
                props.get("province")
                or props.get("province_name")
                or props.get("changwat")
            )
            out["district"] = props.get("district") or props.get("amphoe")
            out["subdistrict"] = (
                props.get("subdistrict")
                or props.get("sub_district")
                or props.get("tambon")
            )

            out["status_text"] = (
                props.get("status_text")
                or props.get("status")
                or props.get("sos_status")
            )

            out["type_name"] = (
                props.get("type_name")
                or props.get("help_type")
                or props.get("disease")
                or props.get("category")
            )

            return pd.Series(out)

        loc_df = df["location"].apply(parse_location)
        df = pd.concat([df, loc_df], axis=1)

    return df


df = load_data()

# -----------------------------
# 2) Sidebar filters (multiselect)
# -----------------------------
st.sidebar.header("Filters")

prov_options = sorted(df["province"].dropna().unique().tolist()) if "province" in df.columns else []
prov_sel = st.sidebar.multiselect("Province", prov_options)

status_col = "status_text" if "status_text" in df.columns else "status"
status_options = sorted(df[status_col].dropna().unique().tolist()) if status_col in df.columns else []
status_sel = st.sidebar.multiselect("Status", status_options)

type_col = "type_name" if "type_name" in df.columns else ("help_type" if "help_type" in df.columns else None)
type_options = sorted(df[type_col].dropna().unique().tolist()) if type_col else []
type_sel = st.sidebar.multiselect("Type / ความต้องการ", type_options)

filtered = df.copy()
if prov_sel:
    filtered = filtered[filtered["province"].isin(prov_sel)]
if status_sel and status_col in filtered.columns:
    filtered = filtered[filtered[status_col].isin(status_sel)]
if type_sel and type_col:
    filtered = filtered[filtered[type_col].isin(type_sel)]

st.caption(f"Total records after filter: **{len(filtered):,} cases**")

# ปุ่มไปยังเว็บ Jitasa
st.markdown(
    """
    <a href="https://jitasa.care/" target="_blank">
        <button style="
            background-color:#ff4b4b;
            color:white;
            padding:0.5rem 1.2rem;
            border:none;
            border-radius:0.6rem;
            font-size:16px;
            cursor:pointer;
        ">
            🆘 ต้องการความช่วยเหลือ - Need Help
        </button>
    </a>
    """,
    unsafe_allow_html=True,
)

# ถ้าไม่มี updated_at ก็จบตรงนี้
if "updated_at" not in filtered.columns:
    st.info("ไม่พบคอลัมน์ 'updated_at' ในข้อมูล")
    st.stop()

tmp = filtered.dropna(subset=["updated_at"]).copy()

# -----------------------------
# 3) Daily cases by updated_at (bar from table)
# -----------------------------
st.subheader("📅 Daily cases trends")

tmp["date_only"] = tmp["updated_at"].dt.normalize()  # ตัดเวลาออก
daily = (
    tmp.groupby("date_only")
    .size()
    .reset_index(name="Total cases")
    .sort_values("date_only")
)
daily["date_str"] = daily["date_only"].dt.strftime("%Y-%m-%d")

daily_chart = (
    alt.Chart(daily)
    .mark_bar()
    .encode(
        x=alt.X("date_str:N", title="Date"),
        y=alt.Y("Total cases:Q"),
        tooltip=["date_str:N", "Total cases:Q"],
    )
    .properties(height=350)
)

st.altair_chart(daily_chart, use_container_width=True)
st.dataframe(daily[["date_str", "Total cases"]], use_container_width=True)

st.markdown("---")

# -----------------------------
# 4) Pie chart – Cases by status
# -----------------------------
st.subheader("Cases by status (percentage)")

if status_col in tmp.columns:
    status_counts = (
        tmp[status_col]
        .fillna("Unknown")
        .value_counts()
        .reset_index()
    )
    status_counts.columns = ["status_text", "Total cases"]
    total = status_counts["Total cases"].sum()
    status_counts["Percent"] = (status_counts["Total cases"] / total) * 100

    pie = (
        alt.Chart(status_counts)
        .mark_arc()
        .encode(
            theta="Total cases:Q",
            color=alt.Color("status_text:N", title="Status"),
            tooltip=[
                "status_text:N",
                "Total cases:Q",
                alt.Tooltip("Percent:Q", format=".1f"),
            ],
        )
    )

    labels = (
        alt.Chart(status_counts)
        .mark_text(radius=120, size=12)
        .encode(
            theta="Total cases:Q",
            text="Total cases:Q",
            color="status_text:N",
        )
    )

    st.altair_chart(pie + labels, use_container_width=True)
    st.dataframe(status_counts, use_container_width=True)
else:
    st.info("ไม่มีคอลัมน์สถานะสำหรับทำกราฟ")

st.markdown("---")


