---
name: gate
description: .gate/config.toml이 있는 프로젝트에서 작업을 state machine으로 진행하는 방법. 그런 프로젝트에서 코드를 고치거나 실험, 기능, 버그 수정을 시작할 때, hook이 행동을 막았을 때, "gate", "stagegate", "state machine", "상태 머신", "next", "계획 파일"을 말할 때 쓴다.
---

# stagegate state machine으로 일하기

이 프로젝트의 작업은 계획 파일과 상태 경로로 진행한다. 구조의 원본은 `.gate/config.toml`이고 `python -X utf8 .gate/gate.py describe`가 지금 경로를 보여 준다. 명령과 원칙은 `.gate/GATE.md`, 프로젝트 목적은 `.gate/PROFILE.md`에 있다.

## 작업 시작

1. `python -X utf8 .gate/gate.py status`로 진행 중인 계획과 할 일을 본다. SessionStart hook이 같은 내용을 넣고, 메시지마다 계획 목록이 한 줄씩 들어온다.
2. 흐름과 관계없는 질문, 조사, 문서 초안 같은 부가 작업은 계획 없이 바로 한다. 하네스는 코드 편집과 계획 상태 진행에만 걸린다.
3. 새 작업은 종류를 골라 시작한다. `python -X utf8 .gate/gate.py start --kind <종류> --title "<제목>"`. 앞 계획에서 이어지면 `--from P###`, 종류의 선택 상태를 켜려면 `--with <선택>`.
4. 출력된 할 일대로 계획 파일을 채운다. 절 제목과 bullet 항목은 틀 그대로 두고 지우지 않는다.

## 상태 진행

- 다음 상태로는 `python -X utf8 .gate/gate.py next P###`로만 넘어간다. 실패하면 빠진 항목이 나오므로 그것을 채운다.
- 검토가 필요한 상태(config.toml에 review가 있는 상태)에서는 사용자에게 묻지 않고 `next P### --review`를 붙인다.
- 조건을 못 채우면 `back P### <상태> --why "..."`로 돌아간다. 같은 상태에 max_visits번 들어오면 멈추고 사용자에게 방향을 묻는다.
- 실행 상태(run = true)에서 오래 걸리는 작업을 띄우기 전에 `run P### --eta HH:MM --basis "<근거>"`를 적고, 답에 남은 시간과 끝나는 시각을 쓴다.
- 사용자가 이번 턴에 멈추라고 하면 그 문장을 `hold P### --quote "<문장>"`으로 적는다. 스스로 멈추려고 쓰지 않는다.
- 그만둘 때는 사용자와 정한 뒤 `abandon P### --why "..."`.

## hook이 막았을 때

- 막은 이유가 출력된다. 이유대로 상태를 진행하거나 커밋한다. `--no-verify`, `core.hooksPath` 변경, 상태 파일 직접 편집으로 피하지 않는다.
- 상태 파일(.gate/state.json)은 gate 명령만 쓴다. 읽기와 git add, commit, diff, checkout, restore는 허용된다. 명령은 다른 명령과 `&&`로 잇지 말고 따로 부른다.
- 구조(상태, 경로, 검사) 자체가 프로젝트와 맞지 않으면 우회하지 말고 evolve skill로 제안서를 써서 사용자 승인을 받는다. config.toml, history, gatekit은 guard가 직접 편집을 막는다.
- 하네스 파일(.gate/gatekit, config.toml, history, library, GATE.md, .githooks)을 바꾸는 커밋은 다른 파일과 섞지 않고 `harness:`로 시작한다.
- SessionStart가 하네스 점검을 권하면 사용자에게 한 번 묻고, 하기로 하면 evolve skill을 쓴다.

## 보고

- 상태를 넘긴 턴의 답에는 넘긴 상태와 다음 상태의 할 일을 쓴다.
- 기록이나 결정 절을 채운 턴의 답에는 그 절 내용을 옮겨 쓴다.
