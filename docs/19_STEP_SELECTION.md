# Step: selection (10개 확정 + 20초 윈도 결정)

## 목적
- 카테고리별 후보에서 총 10개를 선택한다.
- 중복(시간 겹침)을 제거한다.
- 각 하이라이트의 clip_start/clip_end(20초)를 확정한다.

## 입력
- artifacts/rallies.json
- artifacts/scores.json
- artifacts/features.json (앵커 계산에 필요할 수 있음)
- config keys:
  - clips.total, clips.quotas, clips.length_sec
  - clips.pre_roll_sec, clips.post_roll_sec
  - clips.overlap_sec

## 출력
- artifacts/highlights.json
  {
    "clip_length_sec": 20.0,
    "highlights": [
      {
        "rank": 1,
        "category": "reaction",
        "rally_id": 9,
        "clip_start": 120.0,
        "clip_end": 140.0,
        "score": 11.7,
        "reasons": [...]
      }
    ]
  }

## 알고리즘
1) 쿼터만큼 카테고리별 상위 후보를 뽑는다(예: 4/3/3)
2) clip window를 카테고리별 앵커 규칙으로 계산
   - reaction: clip_end = end_refined + post_roll, clip_start = clip_end - 20
   - long_rally: rally 내부에서 "흥분도" 최대 구간을 찾아 20초 윈도 선택
   - impact: impact_peak 또는 ball_speed_peak 최대 시점을 앵커로 20초 윈도 선택
3) overlap_sec 이상 겹치면 점수 낮은 것을 제거하고 다음 후보로 대체
4) 총 10개를 채우지 못하면 남은 자리는 전체 후보에서 점수 높은 순으로 채움

## Acceptance
- 항상 highlights.json 생성(0개일 수도 있음)
- 각 highlight는 clip_end - clip_start == 20초(가능한 한 유지, 영상 끝 경계에서는 클램프 허용)
