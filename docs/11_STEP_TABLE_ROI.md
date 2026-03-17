# Step: table_roi (table detection + user correction)

## 목적
- frame0에서 테이블 4점 폴리곤을 자동 추정하고, 사용자가 수정할 수 있게 한다.
- 결과는 이후 모든 vision 단계의 기준이 된다.

## 입력
- artifacts/frame0.jpg
- config keys:
  - video.warp_width, video.warp_height (워핑 출력 크기)

## 출력 (artifacts)
- artifacts/table_roi.json
  {
    "table_polygon": [[x1,y1],[x2,y2],[x3,y3],[x4,y4]],
    "polygon_order": "clockwise",
    "source": "auto+user",
    "frame_size": {"w":..., "h":...}
  }
- (optional) artifacts/scoreboard_roi.json
  {
    "enabled": true/false,
    "rect": {"x":..., "y":..., "w":..., "h":...}
  }
- debug/frame0_overlay.png (table polygon 그려서 저장)

## 알고리즘(자동 초기값)
- OpenCV 기반:
  1) edge(Canny) -> line(HoughLinesP)
  2) 두 방향성(대략 수평/수직) 선분을 골라 외곽 4개 선 추정
  3) 교차점으로 quad 생성
  4) quad가 비정상(자기교차/너무 작음)이면 fallback:
     - HSV/threshold 기반 큰 contour -> approxPolyDP로 4점 근사
  5) 그래도 실패하면 "중앙 큰 사각형"을 임시값으로 제공(사용자 수정 전제)

## 사용자 수정(UI)
- Streamlit UI에서 polygon을 드래그/재그리기로 수정 가능해야 함.
- scoreboard ROI도 "있으면" 사각형 지정 가능.

## 실패/예외
- table_roi.json이 없으면 이후 vision step은 실패하도록 강제(테이블 ROI 필수)

## Acceptance
- table_polygon이 프레임 내부에 있고 convex 형태
- overlay 이미지에서 폴리곤이 테이블을 대략 감싸고 있음(수정 후)
