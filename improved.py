"""셀러노트 발주팀 기준일 발주안 조회 및 재고 변동 검토.

같은 폴더에 발주규칙_가공데이터.zip을 두고 실행합니다.
CSV 원본의 기준일은 2026-10-27이며 현재 시점의 실시간 재고가 아닙니다.
"""

from __future__ import annotations

from datetime import date, timedelta
from html import escape
from io import BytesIO
from math import ceil
from pathlib import Path
from zipfile import ZipFile

import pandas as pd
import streamlit as st


st.set_page_config(page_title="셀러노트 | 발주 검토", layout="wide")
DATA_DATE = date(2026, 10, 27)
ARCHIVE = Path(__file__).resolve().parent / "발주규칙_가공데이터.zip"

st.markdown("""<style>
.recommend {background:#e5f3ed;border:2px solid #08715b;border-radius:12px;padding:17px 22px}
.recommend b {color:#075943;font-size:2rem}
div[data-testid="stMetric"] {background:#fff;border:1px solid #d9e5df;border-radius:12px;padding:17px 22px;min-height:111px;box-shadow:0 1px 2px rgba(0,0,0,.03)}
.alert-card {padding:.75rem .95rem;border-radius:8px;margin-bottom:.55rem;font-size:.88rem;line-height:1.45}
.alert-red {background:#fce5e4;border-left:5px solid #d93025;color:#7f1d1d}
.alert-orange {background:#ffead8;border-left:5px solid #f59e0b;color:#78350f}
.product-label {font-size:.875rem;margin-bottom:.35rem;color:inherit}
.product-value {box-sizing:border-box;width:100%;border:1px solid #d3d6dc;border-radius:.5rem;background:#f0f2f6;color:#31333f;font-family:inherit;font-size:1rem;line-height:1.6;padding:.5rem .75rem;margin-bottom:1rem}
</style>""", unsafe_allow_html=True)


@st.cache_data
def load_data(path: str, modified: int):
    del modified
    with ZipFile(path) as archive:
        def csv(prefix: str) -> pd.DataFrame:
            members = [x for x in archive.infolist() if not x.is_dir()]
            for member in members:
                name = member.filename
                try:
                    name = name.encode("cp437").decode("utf-8")
                except (UnicodeEncodeError, UnicodeDecodeError):
                    pass
                if Path(name).name.startswith(prefix):
                    return pd.read_csv(BytesIO(archive.read(member)), encoding="utf-8-sig")
            raise FileNotFoundError(f"압축파일 안에 {prefix} CSV가 없습니다.")

        return csv("09_"), csv("01_"), csv("10_"), csv("04_")


if not ARCHIVE.is_file():
    st.error("발주규칙_가공데이터.zip을 improved.py와 같은 폴더에 업로드해 주세요.")
    st.stop()

try:
    plan, master, transit, lots = load_data(str(ARCHIVE), ARCHIVE.stat().st_mtime_ns)
except Exception as exc:
    st.error(f"발주팀 CSV를 읽을 수 없습니다: {exc}")
    st.stop()

plan["SKU ID"] = plan["SKU ID"].astype(str).str.strip()
master["SKU ID"] = master["SKU ID"].astype(str).str.strip()
transit["SKU ID"] = transit["SKU ID"].astype(str).str.strip()
lots["SKU ID"] = lots["SKU ID"].astype(str).str.strip()
plan = plan.set_index("SKU ID", drop=False)
master = master.set_index("SKU ID", drop=False)


def number(value) -> int:
    return int(round(float(value))) if pd.notna(value) else 0


def lot_candidates(sid: str) -> pd.DataFrame:
    """기준일에 이미 입고됐고 만료 전인 과거 로트 후보. 현재 잔량은 미상."""
    rows = lots[lots["SKU ID"] == sid].copy()
    rows["입고완료일"] = pd.to_datetime(rows["입고완료일"], errors="coerce")
    rows["관리일자(소비기한 만료일)"] = pd.to_datetime(rows["관리일자(소비기한 만료일)"], errors="coerce")
    rows = rows[(rows["입고완료일"] <= pd.Timestamp(DATA_DATE)) &
                (rows["관리일자(소비기한 만료일)"] > pd.Timestamp(DATA_DATE))].copy()
    # 원장에 직접 기록된 마감일은 없음. SKU 마스터의 J/소비기한 추정 비율을
    # 로트의 입고 시 잔여기간에 적용한 참고값으로만 표시한다.
    reference = master.loc[sid] if sid in master.index else None
    ratio = (float(reference["판매가능기간 J(일)"]) /
             float(reference["소비기한 역산(일)"])) if reference is not None and pd.notna(reference["판매가능기간 J(일)"]) and pd.notna(reference["소비기한 역산(일)"]) and float(reference["소비기한 역산(일)"]) > 0 else None
    rows["추정 판매 마감일"] = (
        rows["입고완료일"] + pd.to_timedelta(
            ((rows["관리일자(소비기한 만료일)"] - rows["입고완료일"]).dt.days / .9 * ratio).round(), unit="D"
        ) if ratio is not None else pd.NaT
    )
    rows["로트 키"] = rows["입고ID"].astype(str) + " / " + rows["관리일자(소비기한 만료일)"].dt.strftime("%Y-%m-%d")
    return rows.drop_duplicates("로트 키").sort_values("관리일자(소비기한 만료일)")


def scenario(sid: str) -> dict:
    """발주팀이 제공한 고정 주문선·목표량을 이용한 재고 포지션 민감도.

    이 함수는 원 규칙 전체를 재실행하지 않는다. 기준일 발주량은 CSV 원문을 사용한다.
    """
    row = plan.loc[sid]
    original_stock = number(row["창고 재고"])
    original_transit = number(row["운송 중 재고"])
    stock = st.session_state.get(f"stock_{sid}", original_stock)
    underway = st.session_state.get(f"transit_{sid}", original_transit)
    extra_b2b = st.session_state.get(f"b2b_{sid}", 0)
    delayed_qty = st.session_state.get(f"delayed_qty_{sid}", 0)
    position = stock + underway - extra_b2b - delayed_qty
    original_position = original_stock + original_transit
    threshold = float(row["주문선 s"])
    target = float(row["목표량 S"])
    pack = number(row["박스 입수"])
    original_qty = number(row["해상 주문량"])
    if position == original_position and not st.session_state.get(f"scenario_active_{sid}", False):
        quantity = original_qty
    elif pack <= 0:
        quantity = None
    elif position <= threshold or (original_qty > 0 and row["판단"] == "해상 동반"):
        quantity = max(0, ceil((target - position) / pack) * pack)
    else:
        quantity = 0
    return {"stock": stock, "transit": underway, "position": position,
            "quantity": quantity, "original": original_qty,
            "changed": position != original_position or st.session_state.get(f"scenario_active_{sid}", False),
            "extra_b2b": extra_b2b, "delayed_qty": delayed_qty}


def b2b_date_risk(sid: str, stock: int, underway: int, qty: int, when: date, delay: int):
    """원장 ETA와 발주안 일평균 출고를 사용한 기준일 이후의 날짜별 재고 흐름."""
    if qty <= 0:
        return None
    detail = transit[transit["SKU ID"] == sid].sort_values("기준일부터 입항 예정까지(일)")
    if number(detail["운송 중 수량(개)"].sum()) != underway:
        return {"error": "수정된 운송 중 총량을 입고 건별 도착일에 배분할 수 없습니다. 입고 건별 수량과 날짜를 확인해야 출고일 부족량을 계산할 수 있습니다."}
    if detail["기준일부터 입항 예정까지(일)"].isna().any():
        return {"error": "입고 예정일이 비어 있는 운송 건이 있어 날짜별 재고를 계산할 수 없습니다."}
    elapsed = (when - DATA_DATE).days
    if elapsed < 0:
        return {"error": "추가 B2B 출고일이 데이터 기준일 이전입니다."}
    arrivals = {}
    for index, (_, item) in enumerate(detail.iterrows()):
        eta = int(item["기준일부터 입항 예정까지(일)"]) + (delay if index == 0 else 0)
        arrivals[eta] = arrivals.get(eta, 0) + number(item["운송 중 수량(개)"])
    remaining = float(stock)
    first_gap = None
    sales_per_day = float(plan.loc[sid]["하루 판매량 d"])
    for day in range(elapsed + 1):
        remaining += arrivals.get(day, 0)
        daily_shortage = max(0.0, sales_per_day - remaining)
        if daily_shortage > 0 and first_gap is None:
            first_gap = DATA_DATE + timedelta(days=day)
        remaining = max(0.0, remaining - sales_per_day)
        if day == elapsed:
            before_b2b = remaining
            b2b_shortage = max(0.0, qty - before_b2b)
            if b2b_shortage > 0 and first_gap is None:
                first_gap = when
    return {"stock_before_b2b": before_b2b, "b2b_shortage": b2b_shortage,
            "first_gap": first_gap, "daily_demand": sales_per_day}


st.title("발주 검토 대시보드")
st.caption(f"발주팀 가공 데이터 기준일 {DATA_DATE:%Y.%m.%d} · 발주안 {len(plan)}개 SKU · 현재 실시간 수치가 아닙니다")
st.info("운송 중 재고는 발주팀이 수작업으로 추정한 값입니다. 일부 박스 입수와 소비기한 역산에도 가정이 포함돼 있어 실제 발주 전 확인이 필요합니다.")

default = "S2036649" if "S2036649" in plan.index else plan.index[0]
query = st.text_input("SKU ID 검색", value=default, placeholder="예: S2037645").strip().upper()
matches = [sid for sid in plan.index if query and query in sid.upper()]
if not matches:
    st.warning("일치하는 SKU가 발주팀 기준일 발주안에 없습니다. SKU ID를 확인해 주세요.")
    st.stop()
sid = query if query in plan.index else matches[0]
row = plan.loc[sid]
name = str(row["상품명"]) if pd.notna(row["상품명"]) else "상품명 미제공"
st.markdown(
    f'<div class="product-label">선택한 상품</div>'
    f'<input class="product-value" type="text" value="{escape(name)} ({escape(sid)})" readonly aria-label="선택한 상품">',
    unsafe_allow_html=True,
)
if len(matches) > 1 and query != sid:
    st.caption(f"검색 결과 {len(matches)}건 중 {sid}를 표시합니다. 전체 ID를 입력하면 해당 상품을 볼 수 있습니다.")

state = scenario(sid)
left, middle, right = st.columns(3)
left.metric("현재 재고량 (창고 + 운송 중)", f"{state['stock'] + state['transit']:,}개")
middle.markdown(f'<div class="recommend">권고 해상 발주량<br><b>{state["quantity"]:,}개</b></div>' if state["quantity"] is not None else '<div class="recommend">권고 해상 발주량<br><b>계산 불가</b></div>', unsafe_allow_html=True)
right.metric("권고 발주시점", f"{DATA_DATE:%Y.%m.%d} (기준일)" if state["quantity"] else "현재 발주 불필요")
st.caption(f"창고 {state['stock']:,}개 + 운송 중 {state['transit']:,}개 · 발주팀 원안 {state['original']:,}개 · 항공 주문량 {number(row['항공 주문량']):,}개")
if state["changed"]:
    st.warning("변경 후 권고량은 발주팀의 발주 기준·목표 재고·박스 입수를 고정한 시나리오 검토값입니다. 로트별 폐기 상한, 신규 B2B 수요의 분포, 공급처 묶음 발주 등 원 규칙 전체를 다시 계산한 공식 발주안은 아닙니다.")

risky = plan[(plan["창고 재고일수"] < 14) & (plan["하루 판매량 d"] > 0)]
alerted = plan[(plan["항공 주문량"] > 0) | (plan["창고 재고일수"] < 14)].copy()
alerted = alerted.sort_values(["항공 주문량", "창고 재고일수"], ascending=[False, True])
with st.sidebar:
    st.header("담당자 알림 시스템")
    st.caption(f"{DATA_DATE:%Y.%m.%d} 발주팀 발주안 기준 · 확인 대상 {len(alerted)} SKU")
    st.caption("항공 주문량이 있는 SKU와 창고 재고일수 14일 미만 SKU를 표시합니다.")
    if st.button("알림 SKU 권고 목록 보기", width="stretch"):
        st.session_state.show_alert_orders = not st.session_state.get("show_alert_orders", False)
    with st.container(height=380, border=True):
        if alerted.empty:
            st.info("확인할 알림이 없습니다.")
        for _, item in alerted.iterrows():
            urgent = number(item["항공 주문량"]) > 0
            label = "🔴 항공 주문 검토" if urgent else "🟠 창고 재고 확인"
            detail = (f"항공 주문량 {number(item['항공 주문량']):,}개" if urgent
                      else f"창고 재고일수 {float(item['창고 재고일수']):.1f}일")
            st.markdown(
                f'<div class="alert-card {"alert-red" if urgent else "alert-orange"}">'
                f'<strong>{label}</strong><br>{escape(str(item["SKU ID"]))} · {escape(str(item["상품명"]))}<br>'
                f'{detail} · 해상 권고 {number(item["해상 주문량"]):,}개</div>',
                unsafe_allow_html=True,
            )

tab_overview, tab_status, tab_week = st.tabs(["종합 대시보드", "SKU별 발주 상태", "금주 발주 요약"])

with tab_overview:
    st.subheader("발주 시나리오 검토")
    st.caption("기준일 재고와 운송 중 수량을 확인하거나 수정한 뒤, 발주 판단 기준과 발주 후 목표 수준으로 변화량을 검토합니다.")
    c1, c2, c3 = st.columns(3)
    c1.metric("발주팀 판단", str(row["판단"]))
    c2.metric("발주 판단 재고 기준", f"{float(row['주문선 s']):,.2f}개",
              help="창고 재고와 운송 중 재고의 합계가 이 수준 이하이면 신규 발주를 검토합니다. 발주팀 CSV의 '주문선 s'입니다.")
    c3.metric("발주 후 목표 재고 수준", f"{float(row['목표량 S']):,.2f}개",
              help="신규 발주로 채우려는 재고 포지션의 목표입니다. 발주팀 CSV의 '목표량 S'입니다.")
    candidates = lot_candidates(sid)
    saved_lots = st.session_state.get(f"verified_lots_{sid}", [])
    selected_keys = st.multiselect(
        "현재 창고에 남아 있는 입고 로트 선택",
        options=candidates["로트 키"].tolist(),
        default=[x["로트 키"] for x in saved_lots if x["로트 키"] in set(candidates["로트 키"])],
        key=f"selected_lots_{sid}",
        help="입고 ID가 같아도 소비기한이 다르면 별도 행입니다. 과거 입고 기록에 있다고 현재 잔량이 있는 것은 아닙니다.",
    )
    with st.form(f"scenario_{sid}"):
        st.markdown("#### 1단계 · 현재 보유 재고(로트) 및 예정 입고 확인")
        st.caption("소비기한은 입고 원장 값입니다. 판매 마감일은 SKU 마스터의 판매가능기간 J/소비기한 비율로 계산한 추정값이므로 확인·수정하세요. 로트 가용 수량은 입력 전에는 알 수 없습니다.")
        lot_inputs = []
        for key in selected_keys:
            item = candidates[candidates["로트 키"] == key].iloc[0]
            previous = next((x for x in saved_lots if x["로트 키"] == key), {})
            expiry = item["관리일자(소비기한 만료일)"].date()
            estimate = item["추정 판매 마감일"]
            deadline_default = previous.get("판매 마감일", estimate.date() if pd.notna(estimate) else expiry)
            c_lot, c_qty, c_exp, c_dead = st.columns([1.4, 1, 1.1, 1.1])
            c_lot.markdown(f"**입고 로트 {escape(key)}**<br>입고일 {item['입고완료일']:%Y.%m.%d}", unsafe_allow_html=True)
            qty = c_qty.number_input("현재 가용 수량 (개)", min_value=0, value=int(previous.get("수량", 0)), key=f"lot_qty_{sid}_{key}")
            c_exp.markdown(f"**소비기한 만료일**<br>{expiry:%Y.%m.%d}", unsafe_allow_html=True)
            deadline = c_dead.date_input("판매 마감일 (추정·수정)", value=deadline_default, key=f"lot_dead_{sid}_{key}")
            lot_inputs.append({"로트 키": key, "수량": qty, "소비기한": expiry, "판매 마감일": deadline})
        if not selected_keys:
            st.info("현재 보유 로트를 확인하지 않았다면 아래 창고 가용재고 합계를 사용합니다. 이때 로트별 판매·폐기량은 계산하지 않습니다.")
        a, b = st.columns(2)
        stock_input = a.number_input("창고 가용재고 합계 (개) · 로트 미선택 시", min_value=0, value=int(state["stock"]), step=1)
        transit_input = b.number_input("운송 중 재고 (개)", min_value=0, value=int(state["transit"]), step=1)
        details = transit[transit["SKU ID"] == sid].sort_values("기준일부터 입항 예정까지(일)")
        if not details.empty:
            st.caption("발주팀 운송 중 내역: " + " · ".join(f"{number(x['운송 중 수량(개)']):,}개 / D+{number(x['기준일부터 입항 예정까지(일)'])} ({x['근거']})" for _, x in details.iterrows()))
        st.markdown("#### 2단계 · 신규 발주 희망량 및 돌발 변수 입력")
        s1, s2, s3, s4 = st.columns(4)
        planned = s1.number_input("금주 신규 발주 희망 수량 (개)", min_value=0, value=int(st.session_state.get(f"planned_{sid}", state["original"])))
        extra_b2b = s2.number_input("추가 확정 B2B 주문량 (개)", min_value=0, value=int(st.session_state.get(f"b2b_raw_{sid}", 0)), help="기준일 수요예측에 포함되지 않은 추가 주문만 입력하세요.")
        b2b_date = s3.date_input("추가 B2B 출고 예정일", value=st.session_state.get(f"b2b_date_{sid}", DATA_DATE))
        delay = s4.number_input("가장 빠른 예정 입고 지연 (+일)", min_value=0, value=int(st.session_state.get(f"delay_{sid}", 0)), disabled=details.empty)
        submitted = st.form_submit_button("입력값 반영 및 발주 권고 재계산", type="primary", width="stretch")
    if submitted:
        if any(x["판매 마감일"] > x["소비기한"] for x in lot_inputs):
            st.error("판매 마감일은 소비기한 만료일보다 늦을 수 없습니다.")
        elif extra_b2b > 0 and b2b_date < DATA_DATE:
            st.error("추가 B2B 출고 예정일은 데이터 기준일 이후로 입력해 주세요.")
        else:
            st.session_state[f"verified_lots_{sid}"] = lot_inputs
            st.session_state[f"stock_{sid}"] = sum(x["수량"] for x in lot_inputs) if selected_keys else int(stock_input)
            st.session_state[f"transit_{sid}"] = int(transit_input)
            st.session_state[f"planned_{sid}"] = int(planned)
            st.session_state[f"b2b_raw_{sid}"] = int(extra_b2b)
            lead = number(row["리드타임 L(일)"]) + 14
            st.session_state[f"b2b_{sid}"] = int(extra_b2b) if (b2b_date - DATA_DATE).days < lead else 0
            st.session_state[f"b2b_date_{sid}"] = b2b_date
            st.session_state[f"delay_{sid}"] = int(delay)
            if not details.empty and int(delay) > 0:
                earliest = details.iloc[0]
                eta = number(earliest["기준일부터 입항 예정까지(일)"])
                excluded = number(earliest["운송 중 수량(개)"]) if eta <= lead < eta + int(delay) else 0
                st.session_state[f"delayed_qty_{sid}"] = min(int(transit_input), excluded)
            else:
                st.session_state[f"delayed_qty_{sid}"] = 0
            st.session_state[f"scenario_active_{sid}"] = (
                st.session_state[f"stock_{sid}"] != number(row["창고 재고"])
                or int(transit_input) != number(row["운송 중 재고"])
                or st.session_state[f"b2b_{sid}"] > 0
                or st.session_state[f"delayed_qty_{sid}"] > 0
            )
            st.rerun()
    if saved_lots:
        st.caption(f"확인된 로트 {len(saved_lots)}건 · 수량 합계 {state['stock']:,}개를 반영했습니다. 로트별 판매 마감일은 발주량의 폐기 상한 계산에 아직 연결되지 않았습니다.")
    if state["extra_b2b"] or state["delayed_qty"]:
        st.info(f"추가 B2B 수요 {state['extra_b2b']:,}개 · 보호기간 밖으로 밀린 운송 중 수량 {state['delayed_qty']:,}개를 발주 판단 재고에서 차감했습니다. 출고일별 수요 분포와 입고별 소비기한은 이 간이 계산에 반영되지 않습니다.")
    if f"planned_{sid}" in st.session_state and st.session_state.get(f"b2b_raw_{sid}", 0) > 0:
        risk = b2b_date_risk(
            sid, state["stock"], state["transit"], st.session_state[f"b2b_raw_{sid}"],
            st.session_state[f"b2b_date_{sid}"], st.session_state.get(f"delay_{sid}", 0),
        )
        if "error" in risk:
            st.warning(risk["error"])
        else:
            st.markdown("#### 추가 B2B 출고일 재고 확인")
            v1, v2 = st.columns(2)
            v1.metric("추가 주문 반영 전 출고일 예상 잔량", f"{risk['stock_before_b2b']:,.1f}개")
            v2.metric("추가 B2B 주문의 예상 부족량", f"{risk['b2b_shortage']:,.1f}개")
            if risk["first_gap"] is not None:
                st.warning(f"기준일 이후 최초 재고 부족 예상일: {risk['first_gap']:%Y.%m.%d}. 출고일까지 입고되는 건만 더한 결과입니다.")
            air_days = master.loc[sid]["항공 리드타임(일)"] if sid in master.index else float("nan")
            if risk["b2b_shortage"] > 0 and pd.notna(air_days):
                arrival = DATA_DATE + timedelta(days=int(round(float(air_days))))
                st.caption(f"발주팀 SKU 마스터의 항공 리드타임 기준 도착 가능일: {arrival:%Y.%m.%d}. 실제 투입 가능 여부·수량은 확인이 필요합니다.")
            st.caption(f"계산 기준: 2026.10.27 창고 재고, 운송 건별 입고 예정일, 발주안 일평균 출고 {risk['daily_demand']:,.2f}개/일, 입력한 추가 B2B 주문. 일평균 판매량은 날짜별 실제 수요가 아닙니다.")
    if f"planned_{sid}" in st.session_state:
        st.caption(f"담당자 희망량 {st.session_state[f'planned_{sid}']:,}개는 권고량 계산의 입력값이 아니라 비교값입니다.")
    if st.button("발주팀 기준값 복원"):
        for prefix in ("stock", "transit", "verified_lots", "selected_lots", "planned", "b2b", "b2b_raw", "b2b_date", "delay", "delayed_qty", "scenario_active"):
            st.session_state.pop(f"{prefix}_{sid}", None)
        st.rerun()

    if st.session_state.get("show_alert_orders"):
        st.subheader("알림 SKU 발주 권고 결과")
        st.dataframe(alerted[["SKU ID", "상품명", "공급처", "창고 재고", "운송 중 재고", "창고 재고일수", "해상 주문량", "항공 주문량", "판단"]], hide_index=True, width="stretch")

    with st.expander("전체 SKU 발주 권고 결과표"):
        st.dataframe(plan[["SKU ID", "상품명", "공급처", "창고 재고", "운송 중 재고", "재고 포지션 IP", "판단", "해상 주문량", "항공 주문량"]], hide_index=True, width="stretch")

with tab_status:
    st.subheader("선택한 SKU의 발주·입고 근거")
    details = transit[transit["SKU ID"] == sid]
    if details.empty:
        st.info("기준일 발주안에 연결된 운송 중 수작업 내역이 없습니다.")
    else:
        st.dataframe(details[["운송 중 수량(개)", "기준일부터 입항 예정까지(일)", "근거"]], hide_index=True, width="stretch")
        if number(details["운송 중 수량(개)"].sum()) != number(row["운송 중 재고"]):
            st.warning("운송 중 상세 내역 합계와 발주안의 집계값이 다릅니다. 발주안 집계값을 우선 표시했습니다.")
    st.caption("입고 로트 원장은 과거 입고 시점의 수량·관리일자를 기록합니다. 기준일 현재 로트별 잔량과 실제 판매 마감일은 제공되지 않아 계산하지 않습니다.")
    historical = lots[lots["SKU ID"] == sid].copy()
    if historical.empty:
        st.info("이 SKU의 입고 로트 원장이 없습니다.")
    else:
        st.dataframe(historical[["입고ID", "입고완료일", "관리일자(소비기한 만료일)", "검수수량"]], hide_index=True, width="stretch")

with tab_week:
    st.subheader("기준일 발주 물량 요약")
    st.caption("2026.10.27 발주팀 발주안 전체 70개 SKU의 한 시점 집계입니다. 실제 이번 주 확정 발주 내역은 제공되지 않았습니다.")
    sea = plan[plan["해상 주문량"] > 0]
    air = plan[plan["항공 주문량"] > 0]
    k1, k2, k3, k4 = st.columns(4)
    k1.metric("해상 발주 공급처", f"{sea['공급처'].nunique()}곳")
    k2.metric("해상 발주 대상", f"{len(sea)} SKU")
    k3.metric("해상 권고 총수량", f"{number(sea['해상 주문량'].sum()):,}개")
    k4.metric("항공 주문 대상", f"{len(air)} SKU")
    k5, k6 = st.columns(2)
    k5.metric("14일 미만 창고 재고", f"{len(risky)} SKU")
    k6.metric("데이터 기준일", f"{DATA_DATE:%Y.%m.%d}")
    st.caption("폐기 위험 SKU 수는 현재 로트별 잔량과 판매 마감일 자료가 없어 집계할 수 없습니다. 창고 재고일수는 운송 중 재고를 포함하지 않습니다.")
    st.dataframe(sea[["SKU ID", "상품명", "공급처", "판단", "해상 주문량", "항공 주문량"]], hide_index=True, width="stretch")
