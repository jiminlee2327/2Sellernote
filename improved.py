"""셀러노트 발주팀 기준일 발주안 조회 및 재고 변동 검토.

같은 폴더에 발주규칙_가공데이터.zip을 두고 실행합니다.
CSV 원본의 기준일은 2026-10-27이며 현재 시점의 실시간 재고가 아닙니다.
"""

from __future__ import annotations

from datetime import date
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
.selected {background:#f0f6f3;border-left:4px solid #08715b;padding:11px 15px;border-radius:5px;margin:0 0 16px}
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


def scenario(sid: str) -> dict:
    """발주팀이 제공한 고정 주문선·목표량을 이용한 재고 포지션 민감도.

    이 함수는 원 규칙 전체를 재실행하지 않는다. 기준일 발주량은 CSV 원문을 사용한다.
    """
    row = plan.loc[sid]
    original_stock = number(row["창고 재고"])
    original_transit = number(row["운송 중 재고"])
    stock = st.session_state.get(f"stock_{sid}", original_stock)
    underway = st.session_state.get(f"transit_{sid}", original_transit)
    position = stock + underway
    original_position = original_stock + original_transit
    threshold = float(row["주문선 s"])
    target = float(row["목표량 S"])
    pack = number(row["박스 입수"])
    original_qty = number(row["해상 주문량"])
    if position == original_position:
        quantity = original_qty
    elif pack <= 0:
        quantity = None
    elif position <= threshold or (original_qty > 0 and row["판단"] == "해상 동반"):
        quantity = max(0, ceil((target - position) / pack) * pack)
    else:
        quantity = 0
    return {"stock": stock, "transit": underway, "position": position,
            "quantity": quantity, "original": original_qty,
            "changed": position != original_position}


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
st.markdown(f'<div class="selected"><strong>선택한 상품</strong> · {escape(name)} <small>({escape(sid)})</small></div>', unsafe_allow_html=True)
if len(matches) > 1 and query != sid:
    st.caption(f"검색 결과 {len(matches)}건 중 {sid}를 표시합니다. 전체 ID를 입력하면 해당 상품을 볼 수 있습니다.")

state = scenario(sid)
left, middle, right = st.columns(3)
left.metric("재고 포지션 (창고 + 운송 중)", f"{state['position']:,}개")
middle.markdown(f'<div class="recommend">권고 해상 발주량<br><b>{state["quantity"]:,}개</b></div>' if state["quantity"] is not None else '<div class="recommend">권고 해상 발주량<br><b>계산 불가</b></div>', unsafe_allow_html=True)
right.metric("발주 판단 기준일", f"{DATA_DATE:%Y.%m.%d}" if state["quantity"] else "발주 대상 아님")
st.caption(f"창고 {state['stock']:,}개 + 운송 중 {state['transit']:,}개 · 발주팀 원안 {state['original']:,}개 · 항공 주문량 {number(row['항공 주문량']):,}개")
if state["changed"]:
    st.warning("변경 후 권고량은 발주팀의 주문선 s·목표량 S·박스 입수를 고정한 재고 포지션 검토값입니다. 공급처 묶음 발주와 원 규칙 전체를 다시 계산한 공식 발주안은 아닙니다.")

tab_overview, tab_status, tab_week = st.tabs(["종합 대시보드", "SKU별 발주 상태", "금주 발주 요약"])

with tab_overview:
    st.subheader("발주 시나리오 검토")
    st.caption("기준일 재고와 운송 중 수량을 확인하거나 수정한 뒤, 주문선과 목표량을 기준으로 변화량을 검토합니다.")
    c1, c2, c3 = st.columns(3)
    c1.metric("발주팀 판단", str(row["판단"]))
    c2.metric("주문선 s", f"{float(row['주문선 s']):,.2f}개")
    c3.metric("목표량 S", f"{float(row['목표량 S']):,.2f}개")
    with st.form(f"scenario_{sid}"):
        a, b = st.columns(2)
        stock_input = a.number_input("창고 가용재고 (개)", min_value=0, value=int(state["stock"]), step=1)
        transit_input = b.number_input("운송 중 재고 (개)", min_value=0, value=int(state["transit"]), step=1)
        submitted = st.form_submit_button("변경값으로 권고량 검토", type="primary")
    if submitted:
        st.session_state[f"stock_{sid}"] = int(stock_input)
        st.session_state[f"transit_{sid}"] = int(transit_input)
        st.rerun()
    if st.button("발주팀 기준값 복원"):
        st.session_state.pop(f"stock_{sid}", None)
        st.session_state.pop(f"transit_{sid}", None)
        st.rerun()

    st.subheader("담당자 알림 시스템")
    risky = plan[(plan["창고 재고일수"] < 14) & (plan["하루 판매량 d"] > 0)]
    st.caption("창고 재고일수 14일 미만은 확인 대상입니다. 입고 전 실제 품절 여부는 운송 건별 일정과 수요 변동을 확인해야 합니다.")
    if st.button("확인 필요한 SKU 목록 보기"):
        st.session_state.show_alert_orders = not st.session_state.get("show_alert_orders", False)
    with st.container(height=220, border=True):
        if risky.empty:
            st.write("확인 대상이 없습니다.")
        for _, item in risky.sort_values("창고 재고일수").iterrows():
            st.write(f"🔴 {item['SKU ID']} · {item['상품명']} · 창고 재고일수 {item['창고 재고일수']:.1f}일 · 해상 권고 {number(item['해상 주문량']):,}개")
    if st.session_state.get("show_alert_orders"):
        st.dataframe(risky[["SKU ID", "상품명", "공급처", "창고 재고", "운송 중 재고", "창고 재고일수", "해상 주문량", "항공 주문량", "판단"]], hide_index=True, width="stretch")

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
