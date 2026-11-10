# 실험 상태 기계(ml)

실험 하나는 계획 파일 하나(experiments/plans/P###.md)와 정해진 상태 순서를 가진다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 `.flow/flow.py next`가 계획 파일, git 기록, 결과 파일로 검사한다. 판단이 필요한 지점(IDEATE, PLAN)은 `next --review`로 부른 검토자가 본다. 상태별 할 일과 조건의 원본은 `.flow/config.toml`이다.

실험 기록은 experiments/log.csv(첫 칸 실험 번호 E###), 접근법 목록은 progress/ideas.md, 현재 상태는 progress/status.md, 방향 결정은 progress/decisions.md에 둔다.

## 프로젝트 단계와 허용되는 종류

| 단계 | 허용되는 종류 |
|---|---|
| 0 준비, EDA | eda, infra |
| 1 baseline, 주력 확정 | model, tune, eda, infra |
| 2 결합 | new, model, tune, eda, infra |
| 3 동결 | freeze, tune, infra |
| 4 재현, 최종 제출 | repro, infra |

단계 전환은 사용자와 정한 뒤 progress/decisions.md에 그 제목의 항목을 쓰고 `flow stage N --decision "<제목>"`으로 한다.

## 종류별 상태 순서

| 종류 | 뜻 | 상태 순서 |
|---|---|---|
| new | 새 정보원 | RESEARCH, IDEATE, SELECT, PLAN, IMPLEMENT, RUN, RECORD, REPLAN |
| model | 새 모델 | RESEARCH, IDEATE, SELECT, PLAN, IMPLEMENT, RUN, RECORD, REPLAN |
| tune | 튜닝과 재확인 | PLAN, IMPLEMENT, RUN, RECORD, REPLAN |
| freeze | 동결 작업 | PLAN, IMPLEMENT, RUN, RECORD, REPLAN |
| repro | 재현 | PLAN, RUN, RECORD |
| eda | EDA | QUESTION, MEASURE, RECORD, REPLAN |
| infra | 인프라, 버그 수정 | IMPLEMENT, RECORD |

IMPLEMENT는 코드에 새로 넣은 숫자가 "값 근거" 표에 모두 있어야 하고, "표본 실행" 절의 커밋이 대조표 파일을 마지막으로 바꾼 커밋이어야 하며, PLAN에서 검토받은 내용이 그대로여야 넘어간다. RUN은 `flow run` 기록이 있고 결과 파일이 RUN에 들어온 뒤에 만들어져야 넘어간다.

## 명령

| 명령 | 동작 |
|---|---|
| `python -X utf8 .flow/flow.py start --kind model --title "GRU4Rec"` | 계획 파일을 만들고 첫 상태로 시작한다. 앞 계획에서 이어지면 `--from P001` |
| `python -X utf8 .flow/flow.py next P001 [--review]` | 지금 상태의 조건을 검사하고, 통과하면 다음 상태로 옮긴다 |
| `python -X utf8 .flow/flow.py run P001 --eta 23:10 --basis "E099 학습 46분"` | RUN에서 예상 종료 시각과 근거를 적는다. 이 기록이 있어야 서버 실행이 열린다 |
| `python -X utf8 .flow/flow.py back P001 PLAN --why "..."` | 앞 상태로 돌아간다 |
| `python -X utf8 .flow/flow.py abandon P001 --why "..."` | 계획을 그만둔다 |
| `python -X utf8 .flow/flow.py hold P001 --quote "..."` | 사용자가 이번 턴에 멈추라고 한 문장을 적는다 |
| `python -X utf8 .flow/flow.py stage 2 --decision "..."` | 프로젝트 단계를 바꾼다 |
| `python -X utf8 .flow/flow.py status` | 진행 중인 계획과 할 일을 출력한다 |

## 막는 장치

| 장치 | 막는 경우 |
|---|---|
| guard (PreToolUse) | 코드 상태(IMPLEMENT, MEASURE)인 계획이 없는데 config의 code 파일을 고칠 때. 상태 파일과 .git/을 직접 쓸 때. 상태를 flow 명령 말고 다른 방법으로 다룰 때. hook 우회. [remote]의 host를 채웠으면 커밋하지 않은 코드가 있거나 `flow run` 기록이 없을 때의 ssh 실행과 업로드 |
| stop (Stop) | 이 세션이 고친 코드, 하네스, 상태 파일에 커밋하지 않은 변경이 있을 때(이번 턴에 고친 문서는 알리기만 한다). 서버 작업을 띄운 턴의 답에 남은 시간과 끝나는 시각이 없을 때. 이번 턴에 다룬 계획(결과 알림으로 시작한 턴이면 이 세션이 다룬 계획)이 RECORD나 REPLAN에 머물거나, RUN의 결과 파일이 다 생겼을 때. 일반 질문이나 부가 작업 턴은 앞 턴의 계획 상태로 막지 않는다. 5번 연속 막으면 통과시킨다 |
| pre-commit | 커밋할 코드 파일이 코드 상태인 계획의 "행 조건 대조표"에 없을 때 |
| commit-msg | 하네스 파일과 다른 파일이 한 커밋에 섞일 때. 하네스 커밋의 메시지가 `harness:`로 시작하지 않을 때 |
