# TT Highlight Clipper

탁구 경기 영상에서 하이라이트 랠리를 자동으로 감지하고 클립을 추출하는 도구.

## 주요 기능

- **오디오 기반 랠리 감지** — PANNs 모델로 타격음·환호성을 분석하여 랠리 구간을 자동 검출
- **영상 활동량 분석** — 프레임 간 움직임을 측정하여 실제 경기 구간을 필터링
- **랠리 세그멘테이션** — 타격 이벤트를 그룹핑하고, 움직임 기반으로 경계를 보정
- **Streamlit UI** — 비디오 타임라인에서 클립을 시각적으로 편집 (드래그, 리사이즈, 삭제)
- **CLI 파이프라인** — 개별 스텝 또는 전체 파이프라인을 커맨드라인으로 실행

## 파이프라인 스텝

| # | Step | 설명 |
|---|------|------|
| 1 | `preprocess` | 프록시 영상 생성, 오디오 추출, 메타데이터 수집 |
| 2 | `table_roi` | 탁구대 영역(ROI) 검출 |
| 3 | `audio_events` | 타격음·환호성 이벤트 감지 |
| 4 | `video_activity` | 프레임 간 움직임 분석 |
| 5 | `rally_segment` | 랠리 구간 결정 및 경계 보정 |
| 6 | `scoreboard_ocr` | 스코어보드 OCR |
| 7 | `ball_tracking` | 공 궤적 추적 |
| 8 | `features` | 랠리별 피처 추출 |
| 9 | `scoring` | 하이라이트 점수 산출 |
| 10 | `selection` | 최종 클립 선정 |
| 11 | `export` | 개별 클립 및 하이라이트 릴 생성 |

## 요구사항

- Python >= 3.11
- FFmpeg (시스템에 설치)
- Tesseract OCR (scoreboard_ocr 스텝 사용 시)

## 설치

```bash
pip install -e .
pip install -r requirements.txt
```

## 사용법

### CLI

```bash
# 새 작업 생성
tt-highlights init --input video.mp4 --out out/

# 전체 파이프라인 실행
tt-highlights run-all --job out/<job_id>/job.json

# 개별 스텝 실행
tt-highlights step preprocess --job out/<job_id>/job.json

# 실패 시 다음 스텝으로 계속
tt-highlights run-all --job out/<job_id>/job.json --skip-on-fail
```

### Streamlit UI

```bash
streamlit run src/tt_highlights/app.py
```

UI에서 영상을 선택하고 Auto-detect로 랠리를 자동 감지한 뒤, 타임라인에서 클립을 직접 편집하고 내보낼 수 있습니다.

## 프로젝트 구조

```
src/tt_highlights/
├── cli.py               # CLI 엔트리포인트 (click)
├── app.py               # Streamlit UI
├── config.py            # 설정 로드 / 병합
├── job.py               # 작업 생성 / 로드
├── media_server.py      # 비디오 서빙용 HTTP 서버
├── default_config.yaml  # 기본 파이프라인 설정
└── steps/               # 파이프라인 스텝 모듈
    ├── preprocess.py
    ├── table_roi.py
    ├── audio_events.py
    ├── video_activity.py
    ├── rally_segment.py
    ├── scoreboard_ocr.py
    ├── ball_tracking.py
    ├── features.py
    ├── scoring.py
    ├── selection.py
    └── export.py
frontend/
└── index.html           # Streamlit 커스텀 비디오 에디터 컴포넌트
docs/                    # 설계 문서
```

## 설정

`default_config.yaml`에서 각 스텝의 파라미터를 조정할 수 있습니다. 작업 생성 시 복사되며, UI에서도 실시간으로 변경 가능합니다.

주요 설정 그룹: `video`, `audio`, `segmentation`, `ocr`, `ball`, `clips`, `scoring`
