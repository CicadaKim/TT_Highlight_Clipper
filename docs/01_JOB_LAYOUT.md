# Job Layout

한 영상 처리 단위를 "job"이라고 부른다.
job은 동일 입력/동일 config/동일 ROI에 대해 재실행해도 같은 결과가 나오게 설계한다.

## Directory structure
out/<job_id>/
  job.json
  config.yaml (복사본)
  artifacts/
    proxy.mp4
    audio.wav
    frame0.jpg

    table_roi.json
    scoreboard_roi.json (optional)

    audio_events.json
    activity.json
    rallies.json
    ocr_events.json (optional)
    ball_tracks.json (optional)

    features.json
    scores.json
    highlights.json

  exports/
    clips/
      clip_001_<category>.mp4 ...
    highlights_reel.mp4

  debug/
    frame0_overlay.png
    activity_plot.png
    audio_events_plot.png
    rallies_timeline.png
    ball_overlay.mp4 (optional)

## job.json (필수)
job.json은 step runner가 읽는 단일 진입점이다.

예:
{
  "input_video": "/abs/path/input.mp4",
  "out_dir": "/abs/path/out/20260227_abcdef",
  "created_at": "2026-02-27T10:00:00+09:00"
}

## 규칙
- 모든 step은 artifacts/ 안에 자신의 산출물을 "덮어쓰기 가능(idempotent)"하게 생성한다.
- 산출물이 이미 있고, 입력 해시가 같으면 재계산을 스킵할 수 있다(캐시).
- 캐시 스킵 여부는 step 내부에서 판단한다(최소 구현은 항상 재생성해도 됨).
