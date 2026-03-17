# Step: features

## 목적
- 오디오/비디오/랠리/OCR/공 추적 정보를 rally 단위 feature로 통합한다.
- 이후 scoring 단계가 이 파일만 보고 점수를 낼 수 있게 한다.

## 입력
- artifacts/rallies.json
- artifacts/audio_events.json
- artifacts/activity.json
- artifacts/ocr_events.json (optional)
- artifacts/ball_tracks.json (optional)

## 출력
- artifacts/features.json
  {
    "rally_features": [
      {
        "rally_id": 1,
        "duration": ...,
        "impact_count": ...,
        "impact_rate": ...,
        "impact_peak": ...,
        "activity_mean": ...,
        "activity_peak": ...,
        "cheer_near_end": ...,
        "ocr_score_change": 0/1,
        "post_pause": ...,
        "ball_track_quality": ...,
        "ball_speed_peak": ...,
        "ball_accel_spikes": ...,
        "ball_coverage_entropy": ...
      }
    ]
  }

## 알고리즘
- rally 구간에서:
  - impact_count/rate/peak: audio_events 기반
  - activity_mean/peak: activity curve 기반
  - cheer_near_end: end_refined 주변 ±2초에서 cheer 확률/segment score 적분
  - post_pause: end 이후 activity 감소량(간단 지표)
  - ocr_score_change: 해당 rally_id에 OCR 이벤트 있으면 1
- ball 특징:
  - ball_track_quality가 config 기준 미만이면 0 또는 None으로 채움(=게이팅)

## Acceptance
- scoring 단계는 features.json만 있어도 동작
