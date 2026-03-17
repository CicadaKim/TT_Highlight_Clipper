# Glossary

이 프로젝트에서 쓰는 용어는 아래로 고정한다.

## Input video
사용자가 선택한 원본 경기 영상 파일(mp4 등).

## Proxy video
분석용으로 해상도/프레임레이트를 낮춘 영상. 분석 속도와 재현성 확보용.
(예: 720p / 30fps)

## Frame0
영상의 첫 프레임(혹은 첫 유효 프레임). 테이블 ROI 자동 검출 및 사용자 수정에 사용.

## Table ROI (table_polygon)
테이블 영역을 나타내는 4점 폴리곤(시계방향 정렬).
후속 단계에서 homography 계산에 사용.

## Warp / Homography
table_polygon을 정규화된 탑다운-ish 좌표계로 변환하기 위한 변환 행렬.
카메라 흔들림/원근을 줄이고 공 추적 및 활동량 계산을 안정화한다.

## Audio events
오디오 모델로부터 얻는 이벤트:
- impact: 타구(짧은 트랜지언트)
- cheer/clap: 환호/박수(상대적으로 길거나 반복 트랜지언트)

## Activity curve
테이블 워핑 좌표계에서 프레임 차분 기반으로 계산한 시간대별 움직임 강도.

## Rally segment
랠리(포인트) 후보 구간. 시작~끝(초)로 표현.
오디오 이벤트 + activity로 생성하고, (옵션) OCR/cheer로 끝 시점을 보정한다.

## Ball track
랠리 구간에서만 수행되는 공 후보 검출 + 트래킹 결과.
성공률이 낮을 수 있으므로 품질 지표(ball_track_quality)로 게이팅한다.

## Category
하이라이트 카테고리 3종:
- long_rally
- impact
- reaction

## Highlight clip
최종 추출되는 20초 고정 mp4 클립.
selection 단계에서 clip_start/clip_end를 결정한다.
