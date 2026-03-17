# Step: scoring

## 목적
- 카테고리별 점수를 계산해 후보 목록을 만든다.

## 입력
- artifacts/features.json
- config keys:
  - scoring.weights.*
  - clips.quotas (선택 단계에서 사용하지만 여기서도 참조 가능)

## 출력
- artifacts/scores.json
  {
    "candidates": {
      "long_rally": [{"rally_id": 1, "score": 12.3, "reasons": [...]}, ...],
      "impact":    [{"rally_id": 5, "score": 10.1, "reasons": [...]}, ...],
      "reaction":  [{"rally_id": 9, "score": 11.7, "reasons": [...]}, ...]
    }
  }

## 알고리즘
- 각 카테고리별로 weights에 따라 선형 결합(미사용 feature는 0 취급)
- reasons는 상위 기여 feature 이름을 몇 개 기록(디버그/설명용)

## Acceptance
- candidates가 비어도 다음 단계가 안전하게 진행
