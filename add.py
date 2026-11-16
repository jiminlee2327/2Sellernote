import time
import datetime
import numpy as np
import pandas as pd
import streamlit as st
import plotly.graph_objects as go

# ---------------------------------------------------------
# 1. 페이지 설정 및 SELLERNOTE 딥그린 테마 스타일
# ---------------------------------------------------------
st.set_page_config(
    page_title="셀러노트 | 발주 프로그램",
    layout="wide",
    initial_sidebar_state="expanded"
)

st.markdown("""
<style>
    .top-banner {
        background-color: #153D34;
        color: white;
        padding: 1rem 1.5rem;
        border-radius: 12px;
        display: flex;
        justify-content: space-between;
        align-items: center;
        margin-bottom: 1.2rem;
    }
    .brand-title { font-size: 1.35rem; font-weight: 800; letter-spacing: 0.04em; }
    .demo-badge {
        font-size: 0.8rem;
        border: 1px solid #78958A;
        border-radius: 20px;
        padding: 4px 12px;
        color: #E1EFE8;
    }
    .exec-badge {
        display: inline-block;
        background-color: #E1EFE8;
        color: #096351;
        border: 1px solid #A9C4BA;
        padding: 0.3rem 0.85rem;
        border-radius: 20px;
        font-weight: 700;
        font-size: 0.85rem;
    }
    .step-header {
        background-color: #F0F6F3;
        border-left: 4px solid #096351;
        padding: 0.55rem 0.9rem;
        font-weight: 800;
        color: #153D34;
        border-radius: 0 8px 8px 0;
        margin: 0.8rem 0 0.6rem 0;
    }
    .date-preview {
        background-color: #E6F2ED;
        color: #096351;
        padding: 3px 9px;
        border-radius: 6px;
        font-weight: 700;
        font-size: 0.85rem;
        display: inline-block;
        margin-top: 2px;
    }
    .alert-card {
        padding: 0.75rem 0.95rem;
        border-radius: 8px;
        margin-bottom: 0.55rem;
        font-size: 0.88rem;
        line-height: 1.45;
    }
    .alert-red { background-color: #FCE5E4; border-left: 5px solid #D93025; color: #7F1D1D; }
    .alert-orange { background-color: #FFEAD8; border-left: 5px solid #F59E0B; color: #78350F; }
    .alert-yellow { background-color: #FFF3CA; border-left: 5px solid #EAB308; color: #713F12; }
    .alert-blue { background-color: #E2ECFF; border-left: 5px solid #2563EB; color: #1E3A8A; }
    div[data-testid="stMetric"] {
        background-color: #FFFFFF;
        border: 1px solid #D9E5DF;
        padding: 0.9rem 1.1rem;
        border-radius: 12px;
        box-shadow: 0 1px 2px rgba(0,0,0,0.03);
    }
</style>
""", unsafe_allow_html=True)

BASE_DATE = datetime.date(2026, 11, 16)

def fmt_md(days: int) -> str:
    target = BASE_DATE + datetime.timedelta(days=int(days))
    return target.strftime("%m.%d")

# ---------------------------------------------------------
# 2. 초기 예시 데이터 구성
# ---------------------------------------------------------
import os

