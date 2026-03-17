# Step: audio_events (model-based)

## 목적
- 오디오에서 타구(impact), 환호/박수(cheer/clap) 이벤트를 시간축으로 추출한다.

## 입력
- artifacts/audio.wav (없으면 step은 "스킵" 가능)
- config keys (audio.* 전부)
  - model_sr, hop_sec
  - impact_threshold, impact_min_distance_sec
  - cheer_threshold, cheer_merge_gap_sec, cheer_min_len_sec
  - impact_label_contains, cheer_label_contains, clap_label_contains

## 출력 (artifacts)
- artifacts/audio_events.json
  {
    "sr": 32000,
    "hop_sec": 0.1,
    "impact_events": [{"t":..., "score":...}, ...],
    "cheer_segments": [{"start":..., "end":..., "score":..., "type":"cheer|clap"}, ...],
    "label_indices_used": {
      "impact": [..],
      "cheer": [..],
      "clap": [..]
    }
  }
- debug/audio_events_plot.png (impact/cheer 확률 시계열 + 이벤트 표시)

## 알고리즘
- 오디오 모델(SED)로 framewise 확률을 얻는다.
- label 매칭은 "부분 문자열 포함"으로 처리한다.
- impact:
  - local maxima + min distance 적용 + threshold
- cheer/clap:
  - threshold 이상 구간을 segment로 만들고
  - 짧은 gap은 merge
  - 너무 짧은 segment는 제거

## 실패/예외
- audio.wav가 없으면:
  - audio_events.json을 "빈 이벤트"로 생성하고 reason을 기록한다.
  - 이후 segmentation은 video_activity 기반만으로도 동작 가능하게 설계(최소 기능).

## Acceptance
- impact_events가 0개라도 파이프라인이 계속 진행 가능
- plot에서 이벤트가 과도하게 난사되지 않는 수준(튜닝은 다음 단계에서)
