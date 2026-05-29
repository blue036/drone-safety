"""
도심 드론 안전 비행 경로 추천 시스템
Streamlit 메인 앱 - 카카오 주소 검색 버전
"""

import streamlit as st
import folium
from streamlit_folium import st_folium
import pandas as pd
import requests
from risk_calculator import (
    risk_label, get_wind_speed, wind_risk,
    building_risk, no_fly_risk, NO_FLY_ZONES,
    total_risk
)

# ───────────────────────────────────────────
# 페이지 설정
# ───────────────────────────────────────────

st.set_page_config(
    page_title="드론 안전 비행 경로 추천",
    page_icon="🚁",
    layout="wide"
)

st.title("🚁 도심 드론 안전 비행 경로 추천 시스템")
st.caption("국토교통 데이터 활용 경진대회 2026 | 인천광역시 기반")

# ───────────────────────────────────────────
# 카카오 주소 검색 API
# ───────────────────────────────────────────

KAKAO_API_KEY = 'deb5b8a5536bd56574915d1da55bb74e'

def search_address(query):
    """
    카카오 주소 검색 API
    주소 문자열 → (위도, 경도, 전체주소)
    """
    url = 'https://dapi.kakao.com/v2/local/search/address.json'
    headers = {'Authorization': f'KakaoAK {KAKAO_API_KEY}'}
    params = {'query': query, 'size': 1}

    try:
        res = requests.get(url, headers=headers, params=params, timeout=5)
        data = res.json()
        if data.get('documents'):
            doc = data['documents'][0]
            lat = float(doc['y'])
            lon = float(doc['x'])
            addr = doc.get('address_name', query)
            return lat, lon, addr
    except Exception as e:
        st.error(f"주소 검색 오류: {e}")

    # 주소 검색 실패 시 키워드 검색으로 재시도
    url2 = 'https://dapi.kakao.com/v2/local/search/keyword.json'
    try:
        res = requests.get(url2, headers=headers,
                           params={'query': query, 'size': 1}, timeout=5)
        data = res.json()
        if data.get('documents'):
            doc = data['documents'][0]
            lat = float(doc['y'])
            lon = float(doc['x'])
            addr = doc.get('place_name', query)
            return lat, lon, addr
    except Exception as e:
        st.error(f"키워드 검색 오류: {e}")

    return None

# ───────────────────────────────────────────
# session_state 초기화
# ───────────────────────────────────────────

for key in ['analyzed', 'start_coord', 'end_coord', 'start_addr', 'end_addr']:
    if key not in st.session_state:
        st.session_state[key] = False if key == 'analyzed' else None

# ───────────────────────────────────────────
# 사이드바
# ───────────────────────────────────────────

st.sidebar.header("📍 비행 경로 설정")

# 출발지
st.sidebar.subheader("출발지")
start_input = st.sidebar.text_input(
    "주소 또는 장소명 입력",
    placeholder="예: 인천 미추홀구 소성로 72",
    key="start_input"
)
if st.sidebar.button("출발지 검색", use_container_width=True):
    result = search_address(start_input)
    if result:
        lat, lon, addr = result
        st.session_state.start_coord = (lat, lon)
        st.session_state.start_addr  = addr
    else:
        st.sidebar.error("주소를 찾을 수 없어요. 다르게 입력해보세요.")

if st.session_state.start_coord:
    st.sidebar.success(f"✅ {st.session_state.start_addr}")

st.sidebar.markdown("---")

# 도착지
st.sidebar.subheader("도착지")
end_input = st.sidebar.text_input(
    "주소 또는 장소명 입력",
    placeholder="예: 인천 연수구 송도동",
    key="end_input"
)
if st.sidebar.button("도착지 검색", use_container_width=True):
    result = search_address(end_input)
    if result:
        lat, lon, addr = result
        st.session_state.end_coord = (lat, lon)
        st.session_state.end_addr  = addr
    else:
        st.sidebar.error("주소를 찾을 수 없어요. 다르게 입력해보세요.")

if st.session_state.end_coord:
    st.sidebar.success(f"✅ {st.session_state.end_addr}")

st.sidebar.markdown("---")

# 가중치 슬라이더
st.sidebar.subheader("⚙️ 위험도 가중치")
w_building = st.sidebar.slider("건물 밀도",    0.0, 1.0, 0.35, 0.05)
w_nofly    = st.sidebar.slider("비행금지구역", 0.0, 1.0, 0.40, 0.05)
w_wind     = st.sidebar.slider("풍속",         0.0, 1.0, 0.25, 0.05)

total_w = w_building + w_nofly + w_wind
if abs(total_w - 1.0) > 0.01:
    st.sidebar.warning(f"⚠️ 가중치 합계: {total_w:.2f} (1.0 권장)")

# 경로 분석 버튼
if st.sidebar.button("🔍 경로 분석", use_container_width=True, type="primary"):
    if not st.session_state.start_coord:
        st.sidebar.error("출발지를 먼저 검색해주세요!")
    elif not st.session_state.end_coord:
        st.sidebar.error("도착지를 먼저 검색해주세요!")
    else:
        st.session_state.analyzed = True
        st.session_state.params = {
            'start':   st.session_state.start_coord,
            'end':     st.session_state.end_coord,
            'weights': (w_building, w_nofly, w_wind)
        }

# ───────────────────────────────────────────
# 경로 생성
# ───────────────────────────────────────────

STEPS = 10

def make_straight_route(s_lat, s_lon, e_lat, e_lon):
    """일반 경로: 직선"""
    return [
        (s_lat + i/STEPS * (e_lat - s_lat),
         s_lon + i/STEPS * (e_lon - s_lon))
        for i in range(STEPS + 1)
    ]


def make_safe_route(s_lat, s_lon, e_lat, e_lon):
    """안전 경로: 비행금지구역 적극 우회"""
    # 경로상 여러 중간점 체크
    best_offset = 0.0
    for i in range(1, STEPS):
        t = i / STEPS
        lat = s_lat + t * (e_lat - s_lat)
        lon = s_lon + t * (e_lon - s_lon)
        risk = no_fly_risk(lat, lon)
        if risk > 0.3:
            best_offset = max(best_offset, risk * 0.08)

    # 우회 경유지 설정 (남쪽으로 우회)
    mid_lat = (s_lat + e_lat) / 2 - best_offset
    mid_lon = (s_lon + e_lon) / 2

    half = STEPS // 2
    route = []
    for i in range(half + 1):
        t = i / half
        route.append((
            s_lat + t * (mid_lat - s_lat),
            s_lon + t * (mid_lon - s_lon)
        ))
    for i in range(1, half + 1):
        t = i / half
        route.append((
            mid_lat + t * (e_lat - mid_lat),
            mid_lon + t * (e_lon - mid_lon)
        ))
    return route[:STEPS + 1]


def analyze_route(route, wind_speed, weights):
    """경로 위험도 계산"""
    return [total_risk(lat, lon, wind_speed, weights) for lat, lon in route]


# ───────────────────────────────────────────
# 지도 생성
# ───────────────────────────────────────────

def build_map(start, end, straight, safe, str_scores, saf_scores):
    center = ((start[0]+end[0])/2, (start[1]+end[1])/2)
    m = folium.Map(location=center, zoom_start=11)

    # 비행금지구역
    for zone in NO_FLY_ZONES:
        folium.Circle(
            location=[zone['lat'], zone['lon']],
            radius=zone['radius_km'] * 1000,
            color='red', fill=True, fill_opacity=0.15,
            popup=f"🚫 {zone['name']}", tooltip=zone['name']
        ).add_to(m)

    # 일반 경로 (빨간 실선)
    folium.PolyLine(
        straight, color='red', weight=4,
        opacity=0.8, tooltip='🔴 일반 경로'
    ).add_to(m)

    # 안전 경로 (초록 점선)
    folium.PolyLine(
        safe, color='green', weight=4,
        opacity=0.8, dash_array='10', tooltip='🟢 안전 경로'
    ).add_to(m)

    # 일반 경로 위험도 포인트
    for i, (lat, lon) in enumerate(straight):
        label, color = risk_label(str_scores[i])
        folium.CircleMarker(
            [lat, lon], radius=6, color=color,
            fill=True, fill_opacity=0.9,
            popup=folium.Popup(
    f"<b>구간 {i+1}</b><br>위험도: {str_scores[i]}<br>등급: {label}",
    max_width=120
)
        ).add_to(m)

    # 출발 / 도착 마커
    folium.Marker(
        start,
        popup=f'🛫 출발지<br>{st.session_state.start_addr}',
        tooltip='출발지',
        icon=folium.Icon(color='blue', icon='play', prefix='fa')
    ).add_to(m)
    folium.Marker(
        end,
        popup=f'🛬 도착지<br>{st.session_state.end_addr}',
        tooltip='도착지',
        icon=folium.Icon(color='red', icon='flag', prefix='fa')
    ).add_to(m)

    return m


# ───────────────────────────────────────────
# 실시간 기상
# ───────────────────────────────────────────

wind_speed = get_wind_speed()
w_label, _ = risk_label(wind_risk(wind_speed))

c1, c2, c3 = st.columns(3)
with c1:
    st.metric("💨 현재 풍속", f"{wind_speed} m/s")
with c2:
    st.metric("🌡️ 풍속 위험도", w_label)
with c3:
    st.metric("📍 분석 지역", "인천광역시")

st.markdown("---")

# ───────────────────────────────────────────
# 메인 화면
# ───────────────────────────────────────────

if st.session_state.analyzed:
    p       = st.session_state.params
    start   = p['start']
    end     = p['end']
    weights = p['weights']

    straight = make_straight_route(start[0], start[1], end[0], end[1])
    safe     = make_safe_route(start[0], start[1], end[0], end[1])

    str_scores = analyze_route(straight, wind_speed, weights)
    saf_scores = analyze_route(safe,     wind_speed, weights)

    avg_str  = round(sum(str_scores) / len(str_scores), 3)
    avg_safe = round(sum(saf_scores) / len(saf_scores), 3)
    impr     = round((avg_str - avg_safe) / max(avg_str, 0.001) * 100, 1)

    # 요약 카드
    st.subheader("📊 경로 비교 분석")
    r1, r2, r3 = st.columns(3)
    with r1:
        lbl, _ = risk_label(avg_str)
        st.metric("🔴 일반 경로 위험도", avg_str)
        st.caption(f"등급: {lbl}")
    with r2:
        lbl, _ = risk_label(avg_safe)
        st.metric("🟢 안전 경로 위험도", avg_safe)
        st.caption(f"등급: {lbl}")
    with r3:
        st.metric("✅ 위험도 감소율", f"{impr}%",
                  delta=f"-{impr}%", delta_color="inverse")

    # 지도
    st.subheader("🗺️ 비행 경로 지도")
    st.caption("🔴 실선 = 일반 경로  |  🟢 점선 = 안전 경로  |  🔴 원 = 비행금지구역")
    m = build_map(start, end, straight, safe, str_scores, saf_scores)
    st_folium(m, width=900, height=550, returned_objects=[])

    # 구간별 테이블
    st.subheader("📋 구간별 위험도 상세")
    df = pd.DataFrame({
        '구간':            [f"구간 {i+1}" for i in range(len(str_scores))],
        '일반 경로 위험도': str_scores,
        '안전 경로 위험도': saf_scores,
    })
    st.dataframe(df, use_container_width=True)

else:
    # 기본 지도
    st.subheader("🗺️ 인천 비행금지구역 지도")
    st.caption("왼쪽 사이드바에서 출발지/도착지 주소를 입력하고 검색해주세요")

    m = folium.Map(location=[37.4563, 126.7052], zoom_start=10)
    for zone in NO_FLY_ZONES:
        folium.Circle(
            location=[zone['lat'], zone['lon']],
            radius=zone['radius_km'] * 1000,
            color='red', fill=True, fill_opacity=0.15,
            popup=f"🚫 {zone['name']}", tooltip=zone['name']
        ).add_to(m)
    st_folium(m, width=900, height=500, returned_objects=[])