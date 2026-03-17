# Step: export (mp4 생성)

## 목적
- highlights.json에 따라 원본에서 클립을 추출하고 합본을 만든다.

## 입력
- job.json (input_video)
- artifacts/highlights.json

## 출력
- exports/clips/clip_###_<category>.mp4
- exports/highlights_reel.mp4

## 알고리즘
- ffmpeg로 -ss/-to 구간 추출
- 합본은 concat list 방식(코덱 문제 생기면 재인코딩)

## 실패/예외
- 특정 클립 추출 실패 시:
  - 전체 실패로 처리하지 말고 해당 클립만 스킵 + 로그 남김(권장)

## Acceptance
- 최소 1개 이상 선택된 경우 reel이 생성됨
