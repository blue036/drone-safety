"""
위험도 계산 모듈
- 기상청 API 실시간 풍속
- 비행금지구역 (항공안전법 고시 좌표)
- 건물 밀도 (건축물대장 CSV)
"""

import requests
import pandas as pd
import math
from datetime import datetime

# ───────────────────────────────────────────
# 1. 기상청 API
# ───────────────────────────────────────────

API_KEY = 'ec1c7599d3c6283c0d3ca1cf6277d14302e35240a5b5499ab794e0c56f946ca5'

def get_wind_speed(nx=54, ny=124):
    """기상청 초단기실황 API로 풍속 조회"""
    url = 'http://apis.data.go.kr/1360000/VilageFcstInfoService_2.0/getUltraSrtNcst'
    now = datetime.now()
    base_date = now.strftime('%Y%m%d')

    # 정각 10분 이내면 이전 시간 사용
    hour = now.hour
    if now.minute < 10:
        hour = max(hour - 1, 0)
    base_time = f"{hour:02d}00"

    params = {
        'serviceKey': API_KEY,
        'numOfRows': 10,
        'pageNo': 1,
        'dataType': 'JSON',
        'base_date': base_date,
        'base_time': base_time,
        'nx': nx,
        'ny': ny
    }

    try:
        res = requests.get(url, params=params, timeout=5)
        data = res.json()
        items = data['response']['body']['items']['item']
        for item in items:
            if item['category'] == 'WSD':
                return float(item['obsrValue'])
    except Exception as e:
        print(f"기상 API 오류: {e}")
    return 3.0  # 오류 시 기본값


def wind_risk(wind_speed):
    """풍속 → 위험도 (0~1)"""
    if wind_speed < 3:
        return 0.1
    elif wind_speed < 7:
        return 0.4
    elif wind_speed < 10:
        return 0.7
    else:
        return 1.0


# ───────────────────────────────────────────
# 2. 비행금지구역 (항공안전법 고시)
# ───────────────────────────────────────────

NO_FLY_ZONES = [
    {'name': '인천국제공항',           'lat': 37.4602, 'lon': 126.4407, 'radius_km': 9.3,  'risk': 1.0},
    {'name': '김포국제공항',           'lat': 37.5583, 'lon': 126.7906, 'radius_km': 9.3,  'risk': 1.0},
    {'name': '서울도심 비행금지(P73A)', 'lat': 37.5720, 'lon': 126.9794, 'radius_km': 3.7,  'risk': 1.0},
    {'name': '서울도심 비행금지(P73B)', 'lat': 37.5720, 'lon': 126.9794, 'radius_km': 9.3,  'risk': 0.8},
]


def haversine(lat1, lon1, lat2, lon2):
    """두 좌표 간 거리 (km)"""
    R = 6371
    dlat = math.radians(lat2 - lat1)
    dlon = math.radians(lon2 - lon1)
    a = math.sin(dlat/2)**2 + math.cos(math.radians(lat1)) * \
        math.cos(math.radians(lat2)) * math.sin(dlon/2)**2
    return R * 2 * math.asin(math.sqrt(a))


def no_fly_risk(lat, lon):
    """비행금지구역 위험도 (0~1)"""
    max_risk = 0.0
    for zone in NO_FLY_ZONES:
        dist = haversine(lat, lon, zone['lat'], zone['lon'])
        if dist <= zone['radius_km']:
            max_risk = max(max_risk, zone['risk'])
        elif dist <= zone['radius_km'] * 1.5:
            proximity = 1 - (dist - zone['radius_km']) / (zone['radius_km'] * 0.5)
            max_risk = max(max_risk, zone['risk'] * proximity * 0.5)
    return round(max_risk, 3)


# ───────────────────────────────────────────
# 3. 건물 밀도 (CSV 기반)
# ───────────────────────────────────────────

# CSV에서 동별 건물 수 계산 후 정규화한 밀도
_building_density_cache = None

def load_building_density(csv_path='data/01. 기본개요_20260515131448.csv'):
    """건축물대장 CSV → 동별 건물밀도 딕셔너리"""
    global _building_density_cache
    if _building_density_cache is not None:
        return _building_density_cache

    try:
        df = pd.read_csv(csv_path, encoding='utf-8-sig')
        # 대지위치에서 동 이름 추출 (예: "인천광역시 중구 중앙동1가 1-1번지" → "중앙동1가")
        df['동'] = df['대지위치'].str.extract(r'중구\s+(\S+)\s+')
        counts = df['동'].value_counts()
        # 0~1 정규화
        min_c, max_c = counts.min(), counts.max()
        density = ((counts - min_c) / (max_c - min_c)).to_dict()
        _building_density_cache = density
        return density
    except Exception as e:
        print(f"건물 CSV 오류: {e}")
        return {}


def building_risk(lat, lon, dong=None):
    """건물 밀도 위험도 (0~1)"""
    if dong:
        density = load_building_density()
        return round(density.get(dong, 0.5), 3)
    # 동 정보 없으면 위도/경도로 인천 중구 평균값 반환
    return 0.6


# ───────────────────────────────────────────
# 4. 통합 위험도
# ───────────────────────────────────────────

def total_risk(lat, lon, wind_speed, weights=(0.35, 0.40, 0.25), dong=None):
    """
    통합 위험도 (0~1)
    weights: (건물밀도, 비행금지, 풍속)
    """
    b = building_risk(lat, lon, dong) * weights[0]
    n = no_fly_risk(lat, lon)         * weights[1]
    w = wind_risk(wind_speed)         * weights[2]
    return round(min(b + n + w, 1.0), 3)


def risk_label(score):
    """위험도 점수 → (등급, 색상)"""
    if score < 0.3:
        return '안전', '#2ecc71'
    elif score < 0.6:
        return '주의', '#f39c12'
    else:
        return '위험', '#e74c3c'


# ───────────────────────────────────────────
# 테스트
# ───────────────────────────────────────────

if __name__ == '__main__':
    print("=== 드론 비행 위험도 테스트 ===\n")
    wind = get_wind_speed()
    print(f"현재 풍속: {wind} m/s\n")

    tests = [
        ('인천 중구 일반', 37.4740, 126.6219),
        ('인천공항 근처',  37.4602, 126.4407),
        ('인천 강화군',    37.7472, 126.4877),
    ]
    for name, lat, lon in tests:
        score = total_risk(lat, lon, wind)
        label, _ = risk_label(score)
        print(f"{name} → 위험도: {score} [{label}]")