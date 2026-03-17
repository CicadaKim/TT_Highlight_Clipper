# Refactoring Progress

## Step 1: setup 신설 + gate + app/CLI 흐름 수정
- [x] `src/tt_highlights/steps/setup.py` 생성
- [x] `__init__.py` STEP_ORDER 수정 (table_roi → setup)
- [x] `setup_state.json` artifact 도입
- [x] `table_roi.json`에 confidence, frame_id 필드 추가
- [x] `scoreboard_roi.json`에 source, confidence, frame_id 추가
- [x] `app.py _screen_setup()` ROI 입력 UI 통합 (canvas + auto proposal)
- [x] `app.py` Auto-detect에서 table_roi 호출 제거 → setup 완료 여부 확인
- [x] `cli.py` --auto-accept-setup 플래그
- [x] `run_all` setup gate 확인
- Status: **COMPLETE**
- Verification: syntax OK, imports OK, CLI --help shows new flag
- Decision: table_roi.py는 삭제하지 않고 유지 (setup.py가 내부 함수를 import해서 사용)

## Step 2: segment_score 통합 + diagnose 갱신
- Status: IN PROGRESS

## Step 3: tuning artifact 저장/자동 로드
- Status: NOT STARTED

## Step 4: feature 정규화 + scoring 개선
- Status: NOT STARTED

## Step 5: selection hybrid 재구현
- Status: NOT STARTED
