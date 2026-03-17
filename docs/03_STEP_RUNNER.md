# Step Runner

목표: 각 step을 독립 실행할 수 있게 한다.
- step은 job.json + config.yaml + 이전 artifacts만 읽는다.
- step은 artifacts/<name>.json 또는 mp4/png 등 산출물을 쓴다.

## CLI shape (권장)
python -m tt_highlights.cli init --input <video> --out <out_dir>
python -m tt_highlights.cli step preprocess --job <job.json>
python -m tt_highlights.cli step table_roi --job <job.json>
...
python -m tt_highlights.cli run_all --job <job.json>

## Step contract template
각 step 문서는 아래를 포함해야 한다:
1) 목적
2) 입력 (읽는 artifacts + config keys)
3) 출력 (생성하는 artifacts)
4) 알고리즘 요약
5) 실패/예외 처리
6) 디버그 산출물(선택)
7) Acceptance criteria (성공 기준)

## Minimal implementation rule
- 처음엔 step runner는 단순히 "지정 step 함수 호출"만 해도 된다.
- 캐시, 병렬, 부분 재실행 최적화는 나중에 추가해도 된다.
