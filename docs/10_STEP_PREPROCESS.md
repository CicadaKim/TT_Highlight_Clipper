# Step: preprocess

## 목적
- 입력 원본에서 분석용 proxy video, audio wav, frame0를 만든다.
- video metadata(duration/fps/size)를 기록한다.

## 입력
- job.json: input_video, out_dir
- config keys:
  - video.proxy_height, video.proxy_fps
  - audio.model_sr

## 출력 (artifacts)
- artifacts/proxy.mp4
- artifacts/audio.wav (mono, sr=model_sr)
- artifacts/frame0.jpg
- artifacts/video_meta.json
  {
    "duration_sec": ...,
    "fps": ...,
    "width": ...,
    "height": ...,
    "has_audio": true/false
  }

## 알고리즘
- ffmpeg로 proxy 생성 (scale + fps, audio 제거)
- ffmpeg로 audio.wav 추출 (mono + resample)
- ffmpeg 또는 OpenCV로 frame0.jpg 추출

## 실패/예외
- ffmpeg 실패 시 stderr를 그대로 로그로 남기고 step 실패 처리
- audio track이 없으면 audio.wav는 생성하지 않고 has_audio=false로 기록(단, 이후 오디오 기반 기능은 제한)

## 디버그
- debug/frame0_overlay.png는 다음 step에서 생성

## Acceptance
- proxy.mp4 재생 가능
- audio.wav 길이가 대략 video duration과 일치(±1초)
- frame0.jpg 생성됨
