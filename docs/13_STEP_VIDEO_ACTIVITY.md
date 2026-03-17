# Step: video_activity

## 목적
- 테이블 워핑 좌표계에서 움직임 강도(activity curve)를 계산한다.
- 오디오 오탐 제거 및 랠리 구간 정제에 사용한다.

## 입력
- artifacts/proxy.mp4
- artifacts/table_roi.json
- config keys:
  - video.warp_width, video.warp_height, video.activity_fps

## 출력 (artifacts)
- artifacts/activity.json
  {
    "fps": 15,
    "samples": [{"t":..., "activity":...}, ...]
  }
- debug/activity_plot.png

## 알고리즘
- proxy에서 activity_fps로 프레임 샘플링
- 매 프레임:
  1) homography로 워핑
  2) grayscale
  3) abs diff(mean)로 activity 계산
- 1D smoothing(간단 moving average)

## 실패/예외
- table_roi가 없으면 실패(필수)
- 프레임 디코딩 실패 시 해당 구간 스킵하되 전체는 계속 진행(가능하면)

## Acceptance
- 활동량 곡선이 0~1 범위(정규화)
- 랠리처럼 움직임이 많은 구간에서 상대적으로 값이 증가
