import streamlit as st
import pandas as pd
import requests
import json
import altair as alt
import pydeck as pdk

st.set_page_config(
    page_title="TH Flood SOS – Jitasa summary dashboard",
    layout="wide"
)

st.title("🚨 TH Flood SOS – Jitasa summary dashboard")

# -----------------------------
# 1) โหลดข้อมูล + parse location (มี error handling)
# -----------------------------
@st.cache_data(ttl=600)  # 600 วินาที = refresh data จาก API ทุก ~10 นาที
def load_data():
    url = "https://jitasa.care/ajax/v1/sos"

    # 1) ดึงข้อมูลจาก API แบบกัน error
    try:
        resp = requests.get(url, timeout=60)
        resp.raise_for_status()  # ถ้า status code != 200 จะ raise error เลย
        raw = resp.json()
    except Exception as e:
        st.error("❗ ไม่สามารถดึงข้อมูลจาก API jitasa.care ได้ในตอนนี้")
        st.caption(f"(รายละเอียดเทคนิค: {e})")
        return pd.DataFrame()

    # 2) ตรวจโครงสร้าง response ให้ตรงก่อน
    if isinstance(raw, dict) and "data" in raw and isinstance(raw["data"], list):
        records = raw["data"]
    elif isinstance(raw, list):
        records = raw
    else:
        st.error("❗ รูปแบบข้อมูลจาก API ไม่ตรงกับที่คาดไว้ (คีย์ 'data' หรือ list)")
        st.caption(f"ตัวอย่าง response: {str(raw)[:500]}")
        return pd.DataFrame()

    # 3) สร้าง DataFrame
    df = pd.DataFrame(records)

    # 4) parse datetime + ตัด timezone ถ้ามี
    for col in ["created_at", "updated_at", "expired_at", "date"]:
        if col in df.columns:
            df[col] = pd.to_datetime(df[col], errors="coerce")
            if pd.api.types.is_datetime64tz_dtype(df[col]):
                df[col] = df[col].dt.tz_convert("UTC").dt.tz_localize(None)

    # 5) parse location → แตก geometry + properties ออกเป็นคอลัมน์
    if "location" in df.columns:

        def parse_location(loc_obj):
            out = {
                "longitude": None,
                "latitude": None,
                "status": None,
                "status_text": None,
                "type_name": None,
                "province": None,
                "district": None,
                "subdistrict": None,
                "address": None,
                "description": None,
                "sos_status": None,
            }

            # location อาจเป็น string (JSON) หรือ dict
            if isinstance(loc_obj, str):
                try:
                    loc = json.loads(loc_obj)
                except Exception:
                    return pd.Series(out)
            elif isinstance(loc_obj, dict):
                loc = loc_obj
            else:
                return pd.Series(out)

            # geometry → coordinates [lon, lat]
            coords = loc.get("geometry", {}).get("coordinates", [None, None])
            out["longitude"] = coords[0]
            out["latitude"] = coords[1]

            # properties
            props = loc.get("properties", {}) or {}

            out["status"] = props.get("status")
            out["status_text"] = props.get("status_text") or props.get("status")

            out["type_name"] = (
                props.get("type_name")
                or props.get("help_type")
                or props.get("disease")
                or props.get("category")
            )

            out["province"] = (
                props.get("province")
                or props.get("province_name")
                or props.get("changwat")
            )
            out["district"] = (
                props.get("district")
                or props.get("amphoe")
            )
            out["subdistrict"] = (
                props.get("subdistrict")
                or props.get("sub_district")
                or props.get("tambon")
            )

            out["address"] = props.get("address")
            out["description"] = props.get("description")
            out["sos_status"] = props.get("sos_status")

            # fallback mapping
            if out["status"] is None and out["sos_status"] is not None:
                out["status"] = out["sos_status"]
            if out["status_text"] is None and out["status"] is not None:
                out["status_text"] = out["status"]

            return pd.Series(out)

        loc_df = df["location"].apply(parse_location)
        df = pd.concat([df, loc_df], axis=1)

    return df


df = load_data()

# ถ้าโหลดไม่ได้ / API พัง → หยุดแสดงผลต่อ
if df.empty:
    st.stop()

# -----------------------------
# 2) Sidebar filters (multiselect dropdown, default = none)
# -----------------------------
st.sidebar.header("Filters")

prov_options = sorted(df["province"].dropna().unique().tolist()) if "province" in df.columns else []
prov_sel = st.sidebar.multiselect("Province", prov_options)

dist_options = sorted(df["district"].dropna().unique().tolist()) if "district" in df.columns else []
dist_sel = st.sidebar.multiselect("District", dist_options)

subdist_options = sorted(df["subdistrict"].dropna().unique().tolist()) if "subdistrict" in df.columns else []
subdist_sel = st.sidebar.multiselect("Sub-district", subdist_options)

status_col = "status_text" if "status_text" in df.columns else "status"
status_options = sorted(df[status_col].dropna().unique().tolist()) if status_col in df.columns else []
status_sel = st.sidebar.multiselect("Status", status_options)

type_col = "type_name" if "type_name" in df.columns else ("help_type" if "help_type" in df.columns else None)
type_options = sorted(df[type_col].dropna().unique().tolist()) if type_col else []
type_sel = st.sidebar.multiselect("Type / ความต้องการ", type_options)

# -----------------------------
# 3) Apply filters
# -----------------------------
filtered = df.copy()

if prov_sel:
    filtered = filtered[filtered["province"].isin(prov_sel)]
if dist_sel:
    filtered = filtered[filtered["district"].isin(dist_sel)]
if subdist_sel:
    filtered = filtered[filtered["subdistrict"].isin(subdist_sel)]
if status_sel and status_col in filtered.columns:
    filtered = filtered[status_col].isin(status_sel)
    filtered = filtered[filtered[status_col].isin(status_sel)]
if type_sel and type_col:
    filtered = filtered[filtered[type_col].isin(type_sel)]

st.caption(f"Total records after filter: **{len(filtered):,} cases**")

# -----------------------------
# 4) Key Figures
# -----------------------------
st.subheader("🔑 Key Figures")

total_cases = len(filtered)

# cases updated ใน 7 วันที่ผ่านมา
if "updated_at" in filtered.columns:
    recent_mask = filtered["updated_at"] >= (pd.Timestamp.now() - pd.Timedelta(days=7))
    recent_cases = recent_mask.sum()
else:
    recent_cases = None

# done / active
done_words = ["success", "completed", "done", "ช่วยเหลือแล้ว"]
if status_col in filtered.columns:
    done_mask = filtered[status_col].astype(str).str.lower().isin(done_words)
    done_cases = done_mask.sum()
    active_cases = total_cases - done_cases
else:
    done_cases = None
    active_cases = None

c1, c2, c3, c4 = st.columns(4)
c1.metric("Total cases", f"{total_cases:,}")
if active_cases is not None:
    c2.metric("Active / not completed", f"{active_cases:,}")
if done_cases is not None:
    c3.metric("Completed / done", f"{done_cases:,}")
if recent_cases is not None:
    c4.metric("Cases updated in last 7 days", f"{recent_cases:,}")

# -----------------------------
# 5) Tables + chart by type
# -----------------------------
st.markdown("---")
col_left, col_right = st.columns([2, 1.6])

with col_left:
    st.subheader("📍 Cases by District")
    if "district" in filtered.columns:
        dist_counts = (
            filtered.groupby(["province", "district"])
            .size()
            .reset_index(name="Total cases")
            .sort_values("Total cases", ascending=False)
        )
        st.dataframe(dist_counts, use_container_width=True)
    else:
        st.info("No 'district' column found in data.")

with col_right:
    st.subheader("📊 Cases by Status")
    if status_col in filtered.columns:
        status_counts = (
            filtered[status_col]
            .fillna("Unknown")
            .value_counts()
            .reset_index()
        )
        status_counts.columns = ["status_text", "Total cases"]
        st.dataframe(status_counts, use_container_width=True)
    else:
        st.info("No status column found.")

st.markdown("---")

st.subheader("📦 Cases by Need / Type")
if type_col:
    type_counts = (
        filtered[type_col]
        .fillna("Unknown")
        .value_counts()
        .reset_index()
    )
    type_counts.columns = ["type_name", "Total cases"]

    chart_type = (
        alt.Chart(type_counts)
        .mark_bar()
        .encode(
            x="Total cases:Q",
            y=alt.Y("type_name:N", sort="-x"),
            tooltip=["type_name", "Total cases"]
        )
        .properties(height=350)
    )
    st.altair_chart(chart_type, use_container_width=True)
    st.dataframe(type_counts, use_container_width=True)
else:
    st.info("No type / need column found (type_name / help_type).")

st.markdown("---")
st.subheader("🗺️ Map – Case Locations")

if {"latitude", "longitude"}.issubset(filtered.columns):

    map_df = (
        filtered[["latitude", "longitude"]]
        .dropna(subset=["latitude", "longitude"])
        .astype({"latitude": "float64", "longitude": "float64"})
    )

    TH_VIEW_STATE = pdk.ViewState(
        latitude=13.5,
        longitude=101.0,
        zoom=5.2,
        min_zoom=5,
        max_zoom=13,
        pitch=0,
        bearing=0,
    )

    layer = pdk.Layer(
        "ScatterplotLayer",
        data=map_df,
        get_position='[longitude, latitude]',
        get_color=[255, 0, 0],
        get_radius=600,
        pickable=True,
    )

    r = pdk.Deck(
    layers=[layer],
    initial_view_state=TH_VIEW_STATE,
    # Basemap ฟรีจาก Carto ไม่ต้องใช้ key
    map_style="https://basemaps.cartocdn.com/gl/positron-gl-style/style.json",
)

    st.pydeck_chart(r)

else:
    st.warning("No latitude/longitude columns after parsing.")