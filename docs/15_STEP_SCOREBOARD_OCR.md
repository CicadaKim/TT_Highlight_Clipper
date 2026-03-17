# Step: scoreboard_ocr (optional)

## 목적
- 점수판 ROI가 있을 때만 OCR로 점수 변화 시점을 감지한다.
- 감지되면 rallies의 end_refined를 더 정확히 보정한다.
- reaction 점수에 강한 힌트를 제공한다.

## 입력
- artifacts/proxy.mp4
- artifacts/rallies.json
- artifacts/scoreboard_roi.json (없거나 enabled=false면 스킵)
- config keys:
  - ocr.enabled, ocr.sample_fps, ocr.window_pre_sec, ocr.window_post_sec, ocr.whitelist

## 출력
- artifacts/ocr_events.json
  {
    "enabled": true/false,
    "events": [
      {"rally_id": 1, "t": 123.4, "delta": [1,0], "confidence": 0.7}
    ]
  }
- artifacts/rallies.json (end_refined 업데이트된 버전으로 덮어쓰기)
- debug/ocr_samples/ (선택)

## 알고리즘
- 각 rally의 (end 기준) [end-window_pre, end+window_post]에서 sample_fps로 프레임 샘플링
- ROI crop -> threshold -> 숫자만 OCR
- 점수 인식값을 시간순으로 정렬
- 안정화(다수결/중앙값) 후 변화가 처음 생긴 시점 t_change를 이벤트로 기록
- t_change가 신뢰도 기준을 넘으면 rallies.end_refined = t_change 로 업데이트

## 실패/예외
- OCR 실패/무의미한 결과면 해당 rally는 무시(ocr 이벤트 없음)
- OCR이 없더라도 이후 단계는 정상 진행

## Acceptance
- 점수판이 있는 영상에서 일부라도 변화 이벤트가 잡히면 end_refined가 더 정확해짐
- 점수판이 없는 영상에서도 스텝은 “스킵”으로 통과
