# Refactoring Progress

## Step 1: setup 신설 + gate + app/CLI 흐름 수정
- Status: **COMPLETE**
- Decision: table_roi.py는 삭제하지 않고 유지 (setup.py가 내부 함수를 import해서 사용)

## Step 2: segment_score 통합 + diagnose 갱신
- [x] rally_segment.py: conf_audio 재정의 (count*0.5 + quality*0.5, rhythm 제거)
- [x] rally_segment.py: segment_score = conf_audio*0.45 + conf_video_norm*0.35 + rhythm*0.20
- [x] rally_segment.py: segment_flags (low_video, video_confirmed)
- [x] rally_segment.py: video_floor → low_video flag (하드 gate 제거)
- [x] app.py: UI 자체 합산식 제거 → segment_score 사용
- [x] app.py: cv_norm 참조 버그 수정
- [x] diagnose.py: explain_detected_rally 수식 → segment_score 체계로 갱신
- [x] diagnose.py: segment_flags 반영
- [x] app.py: 진단 렌더링 수식 표시 갱신
- Status: **COMPLETE**
- Verification: syntax OK, functions tested

## Step 3: tuning artifact 저장/자동 로드
- Status: IN PROGRESS

## Step 4: feature 정규화 + scoring 개선
- Status: NOT STARTED

## Step 5: selection hybrid 재구현
- Status: NOT STARTED
