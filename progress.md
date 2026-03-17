# Refactoring Progress

## Step 1: setup 신설 + gate + app/CLI 흐름 수정
- Status: **COMPLETE**

## Step 2: segment_score 통합 + diagnose 갱신
- Status: **COMPLETE**

## Step 3: tuning artifact 저장/자동 로드
- [x] parameter_suggestions.json artifact 도입
- [x] feedback_labels.json artifact 도입
- [x] app.py "Apply Suggested Parameters": session override + artifact 저장
- [x] 세션 시작 시 기존 suggestion 표시 + "Load previous suggestions" 버튼
- [x] "Revert suggestions" 버튼
- [x] highlight toggle, delete(exclude) 액션을 feedback_labels.json에 기록
- Status: **COMPLETE**
- Verification: syntax OK, functions import OK

## Step 4: feature 정규화 + scoring 개선
- Status: IN PROGRESS

## Step 5: selection hybrid 재구현
- Status: NOT STARTED
