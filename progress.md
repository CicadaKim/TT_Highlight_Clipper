# Refactoring Progress

## Step 1: setup 신설 + gate + app/CLI 흐름 수정
- Status: **COMPLETE**

## Step 2: segment_score 통합 + diagnose 갱신
- Status: **COMPLETE**

## Step 3: tuning artifact 저장/자동 로드
- Status: **COMPLETE**

## Step 4: feature 정규화 + scoring 개선
- [x] features.py: raw/norm 구조 + flat compat 필드 유지
- [x] features.py: percentile clipping + 0-1 정규화
- [x] features.py: 5개 미만 gate (identity fallback)
- [x] features.py: binary feature (ocr_score_change) 복사
- [x] features.py: ball_features_enabled, impact_times, ball_speed_peak_t 추가
- [x] scoring.py: norm fallback 패턴, reasons에 raw/norm/weight/contribution
- [x] default_config.yaml: normalization + minimums 추가
- [x] selection.py: scoring.minimums 후보 필터링 적용
- Status: **COMPLETE**
- Verification: syntax OK, normalization tested (6 rallies + identity fallback)

## Step 5: selection hybrid 재구현
- Status: IN PROGRESS
