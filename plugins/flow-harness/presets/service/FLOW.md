# 작업 상태 기계(service)

작업 하나는 계획 파일 하나(plans/P###.md)와 정해진 상태 순서를 가진다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 `.flow/flow.py next`가 계획 파일, git 기록, 테스트 명령으로 검사한다. 상태별 할 일과 조건의 원본은 `.flow/config.toml`이다.

## 종류별 상태 순서

| 종류 | 뜻 | 상태 순서 |
|---|---|---|
| feature | 새 기능 | SPEC, PLAN, IMPLEMENT, TEST, RECORD |
| bugfix | 버그 수정 | REPRO, FIX, VERIFY, RECORD |
| refactor | 동작을 바꾸지 않는 구조 변경 | PLAN, IMPLEMENT, TEST, RECORD |
| chore | 설정, 의존성, 문서 같은 잡일 | IMPLEMENT, RECORD |

bugfix는 재현 명령이 REPRO에서 실패하고 FIX에서 통과해야 넘어간다. SPEC과 PLAN은 `next --review`로 부른 검토자(읽기 도구만 쓰는 별도 Claude 호출)가 PASS를 줘야 넘어간다.

## 명령

| 명령 | 동작 |
|---|---|
| `python -X utf8 .flow/flow.py start --kind feature --title "로그인"` | 계획 파일을 만들고 첫 상태로 시작한다. 앞 계획에서 이어지면 `--from P001` |
| `python -X utf8 .flow/flow.py next P001 [--review]` | 지금 상태의 조건을 검사하고, 통과하면 다음 상태로 옮긴다 |
| `python -X utf8 .flow/flow.py back P001 PLAN --why "..."` | 앞 상태로 돌아간다 |
| `python -X utf8 .flow/flow.py abandon P001 --why "..."` | 계획을 그만둔다 |
| `python -X utf8 .flow/flow.py hold P001 --quote "..."` | 사용자가 이번 턴에 멈추라고 한 문장을 적는다. 그 문장이 든 턴에서는 stop hook이 이 계획으로 막지 않는다 |
| `python -X utf8 .flow/flow.py status` | 진행 중인 계획과 할 일을 출력한다 |

## 막는 장치

| 장치 | 막는 경우 |
|---|---|
| guard (PreToolUse) | 코드 상태(IMPLEMENT, REPRO, FIX)인 계획이 없는데 config의 code 파일을 고칠 때. 상태 파일과 .git/을 직접 쓸 때. 상태를 flow 명령 말고 다른 방법으로 다룰 때. hook 우회 |
| stop (Stop) | 이 세션이 고친 파일에 커밋하지 않은 변경이 있을 때. 이 세션이 다룬 계획이 RECORD에 머물 때. 5번 연속 막으면 통과시킨다 |
| pre-commit | 커밋할 코드 파일이 코드 상태인 계획의 "변경 파일" 표에 없을 때 |
| commit-msg | 하네스 파일(.flow/flowkit, config.toml, 틀, .githooks)과 다른 파일이 한 커밋에 섞일 때. 하네스 커밋의 메시지가 `harness:`로 시작하지 않을 때 |

여러 세션이 한 저장소에서 동시에 일해도 stop hook은 이 세션이 고친 파일과 다룬 계획만 본다.
