# UI: Streamlit (최소 기능 정의)

## 목적
- 첫 프레임에서 테이블 ROI 자동 결과를 보여주고 사용자가 수정한다.
- 점수판 ROI(옵션)를 지정한다.
- step 실행 및 결과 미리보기/피드백 저장을 제공한다.

## 필수 화면
1) 입력 영상 선택/업로드 + job 생성(init)
2) frame0 표시
3) 테이블 ROI:
   - 자동 검출 결과 overlay
   - 사용자 수정(폴리곤 편집)
4) 점수판 ROI(옵션):
   - 사각형 지정 + enabled 토글
5) 실행:
   - run_all 또는 step별 실행 버튼
6) 결과:
   - 카테고리/점수/미리보기(간단 재생)
   - 제외/고정(피드백 저장: artifacts/feedback.json)
7) export 버튼

## 피드백(E2)
- artifacts/feedback.json
  {
    "excluded_rally_ids": [...],
    "pinned_rally_ids": [...]
  }
- selection 단계는 feedback을 읽어서:
  - excluded는 후보에서 제거
  - pinned는 우선 포함(쿼터 초과 시 다른 후보를 밀어냄)
