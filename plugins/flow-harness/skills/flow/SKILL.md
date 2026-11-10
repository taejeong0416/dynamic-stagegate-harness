---
name: flow
description: .flow/config.toml이 있는 프로젝트에서 작업을 상태 기계로 진행하는 방법. 그런 프로젝트에서 코드를 고치거나 실험, 기능, 버그 수정을 시작할 때, hook이 행동을 막았을 때, "flow", "상태 기계", "next", "계획 파일"을 말할 때 쓴다.
---

# flow 상태 기계로 일하기

이 프로젝트의 작업은 계획 파일과 상태 순서로 진행한다. 상태와 조건의 원본은 `.flow/config.toml`, 사람이 읽는 설명은 `.flow/FLOW.md`다.

## 작업 시작

1. `python -X utf8 .flow/flow.py status`로 진행 중인 계획과 할 일을 본다. SessionStart와 UserPromptSubmit hook이 같은 내용을 넣어 준다.
2. 새 작업은 종류를 골라 시작한다. `python -X utf8 .flow/flow.py start --kind <종류> --title "<제목>"`. 앞 계획에서 이어지면 `--from P###`.
3. 출력된 할 일대로 계획 파일을 채운다. 절 제목과 bullet 항목은 틀 그대로 두고 지우지 않는다.

## 상태 진행

- 다음 상태로는 `python -X utf8 .flow/flow.py next P###`로만 넘어간다. 실패하면 빠진 항목이 나오므로 그것을 채운다.
- 검토가 필요한 상태(config.toml에 review가 있는 상태)에서는 사용자에게 묻지 않고 `next P### --review`를 붙인다.
- 조건을 못 채우면 `back P### <상태> --why "..."`로 돌아간다. 같은 상태에 max_visits번 들어오면 멈추고 사용자에게 방향을 묻는다.
- 실행 상태(run = true)에서 오래 걸리는 작업을 띄우기 전에 `run P### --eta HH:MM --basis "<근거>"`를 적고, 답에 남은 시간과 끝나는 시각을 쓴다.
- 사용자가 이번 턴에 멈추라고 하면 그 문장을 `hold P### --quote "<문장>"`으로 적는다. 스스로 멈추려고 쓰지 않는다.
- 그만둘 때는 사용자와 정한 뒤 `abandon P### --why "..."`.

## hook이 막았을 때

- 막은 이유가 출력된다. 이유대로 상태를 진행하거나 커밋한다. `--no-verify`, `core.hooksPath` 변경, 상태 파일 직접 편집으로 피하지 않는다.
- 상태 파일(.flow/state.json)은 flow 명령만 쓴다. 읽기와 git add, commit, diff, checkout, restore는 허용된다. 명령은 다른 명령과 `&&`로 잇지 말고 따로 부른다.
- 조건 자체가 이 작업에 맞지 않으면 우회하지 말고 `.flow/config.toml`을 고친다. 하네스 파일(.flow/flowkit, config.toml, 틀, FLOW.md, .githooks)을 고치는 커밋은 다른 파일과 섞지 않고 `harness:`로 시작한다.

## 보고

- 상태를 넘긴 턴의 답에는 넘긴 상태와 다음 상태의 할 일을 쓴다.
- 기록이나 결정 절을 채운 턴의 답에는 그 절 내용을 옮겨 쓴다.
