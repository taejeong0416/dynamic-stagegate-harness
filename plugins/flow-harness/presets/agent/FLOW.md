# 작업 상태 기계(agent)

작업 하나는 계획 파일 하나(plans/P###.md)와 정해진 상태 순서를 가진다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 `.flow/flow.py next`가 계획 파일, git 기록, eval 결과 파일, 명령 실행으로 검사한다. 상태별 할 일과 조건의 원본은 `.flow/config.toml`이다.

에이전트 동작은 눈으로 보고 좋아졌다고 판단하기 쉬워서, 바꾸기 전에 eval 케이스와 합격 기준을 커밋하고 바꾼 뒤 같은 eval로 잰다. 결과는 미리 적은 점수 경계 중 하나로 다음 행동을 고른다.

## 종류별 상태 순서

| 종류 | 뜻 | 상태 순서 |
|---|---|---|
| behavior | 새 동작이나 도구 | SPEC, EVALSET, IMPLEMENT, EVAL, RECORD, REPLAN |
| prompt | 프롬프트나 설정 조정 | PLAN, IMPLEMENT, EVAL, RECORD, REPLAN |
| bugfix | 실패 사례 수정 | REPRO, FIX, VERIFY, RECORD |
| chore | 설정, 의존성, 문서 같은 잡일 | IMPLEMENT, RECORD |

EVALSET은 구현 전에 eval 케이스를 커밋하는 상태다. EVAL은 결과 파일이 이 상태에 들어온 뒤에 새로 만들어져야 넘어간다. SPEC과 PLAN은 `next --review`로 부른 검토자가 PASS를 줘야 넘어간다.

## 명령

| 명령 | 동작 |
|---|---|
| `python -X utf8 .flow/flow.py start --kind behavior --title "웹 검색 도구"` | 계획 파일을 만들고 첫 상태로 시작한다. 앞 계획에서 이어지면 `--from P001` |
| `python -X utf8 .flow/flow.py next P001 [--review]` | 지금 상태의 조건을 검사하고, 통과하면 다음 상태로 옮긴다 |
| `python -X utf8 .flow/flow.py run P001 --eta 23:10 --basis "지난 eval 12분"` | EVAL에서 예상 종료 시각과 근거를 적는다 |
| `python -X utf8 .flow/flow.py back P001 PLAN --why "..."` | 앞 상태로 돌아간다 |
| `python -X utf8 .flow/flow.py abandon P001 --why "..."` | 계획을 그만둔다 |
| `python -X utf8 .flow/flow.py hold P001 --quote "..."` | 사용자가 이번 턴에 멈추라고 한 문장을 적는다 |
| `python -X utf8 .flow/flow.py status` | 진행 중인 계획과 할 일을 출력한다 |

## 막는 장치

| 장치 | 막는 경우 |
|---|---|
| guard (PreToolUse) | 코드 상태(EVALSET, IMPLEMENT, REPRO, FIX)인 계획이 없는데 config의 code 파일(프롬프트, 도구, evals 포함)을 고칠 때. 상태 파일과 .git/을 직접 쓸 때. 상태를 flow 명령 말고 다른 방법으로 다룰 때. hook 우회 |
| stop (Stop) | 이 세션이 고친 파일에 커밋하지 않은 변경이 있을 때. 이 세션이 다룬 계획이 RECORD나 REPLAN에 머물 때. 5번 연속 막으면 통과시킨다 |
| pre-commit | 커밋할 코드 파일이 코드 상태인 계획의 "변경 파일" 표에 없을 때 |
| commit-msg | 하네스 파일과 다른 파일이 한 커밋에 섞일 때. 하네스 커밋의 메시지가 `harness:`로 시작하지 않을 때 |
