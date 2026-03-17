# Step: rally_segment

## 목적
- audio_events(impact) + activity를 이용해 랠리 구간(start/end)을 만든다.
- cheer 이벤트로 "종료 시점" 신뢰도를 보강한다.

## 입력
- artifacts/audio_events.json
- artifacts/activity.json
- config keys:
  - segmentation.impact_gap_max_sec
  - segmentation.min_impacts
  - segmentation.min_rally_duration_sec
  - segmentation.end_grace_sec
  - segmentation.activity_min_mean

## 출력 (artifacts)
- artifacts/rallies.json
  {
    "rallies": [
      {
        "id": 1,
        "start": ...,
        "end": ...,
        "end_refined": ...,
        "reason_end_refined": "cheer|gap_timeout",
        "impact_count": ...,
        "conf_audio": ...,
        "conf_video": ...
      }
    ]
  }
- debug/rallies_timeline.png

## 알고리즘(상태 머신)
- impact_events가 충분히 연속되면 IN_RALLY로 전환
- 마지막 impact 이후 시간이 impact_gap_max + end_grace를 넘으면 종료
- 필터링:
  - duration >= min_rally_duration
  - impact_count >= min_impacts (오디오가 비어있으면 이 조건 완화 가능)
- 비디오 정제:
  - 해당 구간 activity mean < activity_min_mean 이면 제거
- 종료 보정(cheer):
  - 종료 근처(±2초)에 cheer segment가 있으면 end_refined를 cheer 시작/피크 근처로 이동

## 실패/예외
- impact_events가 거의 없는 경우:
  - "activity 기반 후보 생성" fallback을 넣을 수 있음(선택)
  - 최소 구현에서는 rallies가 0개일 수 있음(그 경우 export는 빈 결과)

## Acceptance
- rallies가 0개여도 시스템은 크래시 없이 highlights.json을 생성(빈 목록)
- 일반 영상에서는 rallies가 여러 개 생성되고, 타임라인이 휴식 구간을 건너뛰는 경향
