# Step: ball_tracking (optional, quality-gated)

## 목적
- 랠리 구간에서만 공 후보 검출 + 트래킹을 수행한다.
- 실패 가능성이 있으므로 ball_track_quality로 게이팅한다.
- 성공한 rally에 한해 ball 기반 특징(speed/coverage 등)을 추가로 제공한다.

## 입력
- artifacts/proxy.mp4
- artifacts/table_roi.json
- artifacts/rallies.json
- config keys:
  - ball.enabled, ball.detection_fps
  - ball.diff_threshold, min_area, max_area, min_circularity, max_aspect_ratio
  - ball.max_jump_px, ball.max_misses, ball.quality_min_ratio
  - video.warp_width, video.warp_height

## 출력
- artifacts/ball_tracks.json
  {
    "enabled": true/false,
    "tracks": [
      {
        "rally_id": 1,
        "quality": 0.62,
        "best_track": [{"t":..., "x":..., "y":..., "conf":...}, ...]
      }
    ]
  }
- debug/ball_overlay.mp4 (선택)

## 알고리즘(권장 최소 구현)
- 워핑 좌표계에서 frame diff로 움직이는 작은 blob 후보 생성
- contour filter:
  - area 범위
  - circularity >= threshold
  - aspect ratio <= threshold
- 간단한 트래킹:
  - Kalman + nearest/hungarian 매칭 (구현 난이도에 따라 nearest부터 시작 가능)
  - max_jump_px로 순간이동 제거
  - max_misses 초과 시 트랙 종료
- rally마다 best track 선택:
  - detection ratio 최대 / 평균 conf 최대
- quality 게이트:
  - quality < quality_min_ratio면 해당 rally의 ball 특징은 “미사용” 처리(다음 step에서)

## 실패/예외
- enabled=false면 빈 파일 생성 후 스킵
- 어떤 rally에서도 트랙이 안 나와도 정상(quality=0)

## Acceptance
- 공 추적이 잘 되는 영상에서 일부 rally는 quality가 의미 있게 상승
- 공 추적이 망가져도 전체 파이프라인이 계속 진행
