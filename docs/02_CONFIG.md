# Config (config.yaml)

config는 "문맥 코드" 없이 각 step이 필요한 파라미터만 직접 참조한다.

## Recommended default keys

video:
  proxy_height: 720
  proxy_fps: 30
  warp_width: 640
  warp_height: 356
  activity_fps: 15

audio:
  model_sr: 32000
  hop_sec: 0.1
  impact_threshold: 0.25
  impact_min_distance_sec: 0.12
  cheer_threshold: 0.35
  cheer_merge_gap_sec: 0.4
  cheer_min_len_sec: 0.5
  # label matching by substring
  impact_label_contains: ["Knock", "Tap", "Click", "Slap", "Thump", "Bang"]
  cheer_label_contains: ["Cheering", "Applause", "Crowd"]
  clap_label_contains: ["Clapping"]

segmentation:
  impact_gap_max_sec: 1.2
  min_impacts: 4
  min_rally_duration_sec: 2.5
  end_grace_sec: 1.8
  activity_min_mean: 0.05  # 초기값(현장 튜닝 대상)

ocr:
  enabled: true
  sample_fps: 5
  window_pre_sec: 1.0
  window_post_sec: 2.0
  whitelist: "0123456789"

ball:
  enabled: true
  detection_fps: 30
  diff_threshold: 25
  min_area: 20
  max_area: 260
  min_circularity: 0.45
  max_aspect_ratio: 1.8
  max_jump_px: 45
  max_misses: 7
  quality_min_ratio: 0.4

clips:
  total: 10
  quotas: { long_rally: 4, impact: 3, reaction: 3 }
  length_sec: 20.0
  pre_roll_sec: 1.0
  post_roll_sec: 1.5
  overlap_sec: 10.0

scoring:
  weights:
    long_rally: { duration: 1.0, impact_count: 0.7, activity_mean: 0.5, ball_coverage_entropy: 0.4 }
    impact:    { impact_peak: 1.2, activity_peak: 0.8, ball_speed_peak: 0.9, ball_accel_spikes: 0.5 }
    reaction:  { cheer_near_end: 1.5, ocr_score_change: 1.8, post_pause: 0.4 }

## 규칙
- step은 "자기 키만" 읽는다.
- step 문서에 "이 키들을 읽는다"가 명시되어야 한다.
