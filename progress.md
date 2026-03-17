# Refactoring Progress

## Step 1: setup 신설 + gate + app/CLI 흐름 수정
- Status: **COMPLETE** (commit: feat(step-1))

## Step 2: segment_score 통합 + diagnose 갱신
- Status: **COMPLETE** (commit: feat(step-2))

## Step 3: tuning artifact 저장/자동 로드
- Status: **COMPLETE** (commit: feat(step-3))

## Step 4: feature 정규화 + scoring 개선
- Status: **COMPLETE** (commit: feat(step-4))

## Step 5: selection hybrid 재구현
- [x] _compute_window: 70% 기준으로 dynamic / anchored_fixed_length 분기
- [x] anchor 규칙: reaction→end_refined, impact→impact_peak_t→ball_speed_peak_t→midpoint, long_rally→densest_window→midpoint
- [x] highlights.json 메타데이터: clip_mode, clip_length_sec, anchor_t, anchor_reason
- [x] overall clip_mode: dynamic | anchored_fixed_length | hybrid
- [x] overlap 제거: 최종 clip window 기준 유지
- [x] pinned/excluded feedback 기존 동작 유지
- Status: **COMPLETE**
- Verification: syntax OK, 5 test cases passed (dynamic, anchored impact/reaction/long_rally, densest_window)

## 전체 완료 요약

모든 5단계 구현 완료. 변경된 파일:

| 파일 | 변경 내용 |
|------|-----------|
| `steps/setup.py` | 신규 - 테이블/스코어보드 ROI auto-propose + setup_state gate |
| `steps/__init__.py` | STEP_ORDER: table_roi → setup |
| `steps/rally_segment.py` | conf_audio 재정의, segment_score/segment_flags 추가 |
| `steps/features.py` | raw/norm 구조, percentile normalization, ball_speed_peak_t/impact_times |
| `steps/scoring.py` | norm fallback, 구조화된 reasons (raw/norm/weight/contribution) |
| `steps/selection.py` | hybrid window (dynamic/anchored), anchor 규칙, minimums 필터 |
| `cli.py` | --auto-accept-setup 플래그, setup gate in run_all |
| `app.py` | ROI canvas editor, segment_score 사용, suggestion/feedback artifacts |
| `diagnose.py` | segment_score 수식, segment_flags 반영 |
| `default_config.yaml` | scoring.normalization, scoring.minimums 추가 |

## 남은 리스크

1. **streamlit-drawable-canvas 의존성**: canvas 기반 ROI 편집은 streamlit-drawable-canvas가 설치되어야 동작. requirements.txt에 이미 포함되어 있음.
2. **table_roi.py 미사용 경로**: STEP_ORDER에서 제거되었으나, setup.py가 내부 함수를 import해서 사용하므로 파일 자체는 유지. `step table_roi` 직접 호출은 이제 실패함 (STEP_ORDER에 없으므로).
3. **scoring.reasons 구조 변경**: reasons가 string list에서 dict list로 변경됨. export나 UI에서 reasons를 표시하는 코드가 있다면 dict 형식 대응 필요.
4. **features.json 구조 변경**: raw/norm 구조가 추가되었으나 flat compat 필드도 유지하여 하위 호환성 확보. 추후 flat 필드 제거 시 scoring.py의 fallback 패턴이 자동 대응.
5. **anchored clip이 video boundary에 걸리는 경우**: clamp만 하고 길이를 보정하지 않으므로, 영상 시작/끝 근처에서는 clip이 짧아질 수 있음.
