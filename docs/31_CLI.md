# CLI

## Commands
1) init
python -m tt_highlights.cli init --input <video> --out <out_base_dir>
-> job.json 생성, config.yaml 복사

2) step 실행
python -m tt_highlights.cli step preprocess --job <job.json>
python -m tt_highlights.cli step table_roi --job <job.json>
...

3) 전체 실행
python -m tt_highlights.cli run_all --job <job.json>

## 규칙
- step은 실패 시 non-zero exit
- step은 성공 시 artifacts를 남김
- run_all은 중간 step 실패 시 중단(기본), 옵션으로 skip-on-fail 가능
