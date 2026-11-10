# 작업 상태 기계

작업 하나는 계획 파일 하나와 정해진 상태 경로를 가진다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 `.gate/gate.py next`가 계획 파일, git 기록, 테스트나 eval 명령, 결과 파일로 검사한다. 판단이 필요한 상태는 `next --review`로 부른 검토자(읽기 도구만 쓰는 별도 Claude 호출)가 PASS를 줘야 넘어간다.

지금 구조(종류, 상태 경로, 상태별 검사)는 `python -X utf8 .gate/gate.py describe`가 `.gate/config.toml`에서 바로 출력하고, `describe --mermaid`는 종류마다 경로를 Mermaid 상태 다이어그램으로 출력한다. 프로젝트의 목적과 검증 방법은 `.gate/PROFILE.md`에 있다.

## 작업 명령

| 명령 | 동작 |
|---|---|
| `start --kind <종류> --title "<제목>" [--with <선택>] [--from P001]` | 계획 파일을 만들고 첫 상태로 시작한다. `--with`는 종류의 options에 있는 선택 상태를 켠다 |
| `next P001 [--review]` | 지금 상태의 조건을 검사하고, 통과하면 조건이 맞는 다음 상태로 옮긴다. 조건이 맞지 않는 상태는 건너뛰고 기록한다 |
| `back P001 <상태> --why "..."` | 앞 상태로 돌아간다 |
| `run P001 --eta HH:MM --basis "..."` | 실행 상태에서 예상 종료 시각과 근거를 적는다 |
| `abandon P001 --why "..."` | 계획을 그만둔다 |
| `hold P001 --quote "..."` | 사용자가 이번 턴에 멈추라고 한 문장을 적는다. 그 문장이 든 턴에서는 stop hook이 이 계획으로 막지 않는다 |
| `stage <n> --decision "..."` | 프로젝트 단계를 바꾼다(stages가 있는 구조) |
| `status` | 진행 중인 계획과 할 일을 출력한다 |

명령은 모두 `python -X utf8 .gate/gate.py` 뒤에 붙인다.

## 구조 바꾸기

구조는 프로젝트가 자라면서 바뀐다. 새 폴더나 산출물이 생기면 새 상태나 경로가 필요하고, 형식만 채우고 지나가는 상태는 빼거나 조건을 붙인다. 구조를 바꾸는 길은 하나다.

1. `diagnose`로 상태별 기록(들어온 횟수, back, 건너뜀, 머문 시간, 절 글자 수, 검토 FAIL 이유), hook이 막은 기록, 저장소 변화를 본다.
2. `.gate/proposals/H###.md`에 제안서를 쓴다. "바꾸는 것" 표에 항목마다 근거를 쓰고, "진행 중인 계획" 절에 영향을 쓰고, "새 구조" 절에 새 config 전체를 toml 블록으로 넣는다. version은 지금보다 1 크다.
3. `check-proposal H### --review`가 lint, 지난 계획 재생, diff를 `H###.diag.md`에 쓰고 검토자를 부른다. 검토자는 상태나 검사를 근거 없이 빼거나 약하게 한 곳을 찾는다.
4. 사용자에게 바꾸는 것과 근거를 보이고 승인을 받는다.
5. `adopt H### --approved "<사용자가 승인한 문장>"`으로 적용한다. guard가 그 문장이 이번 턴 사용자 입력에 있는지 본다. 앞 구조는 `.gate/history/v<N>.toml`에 남고, 진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다.

`lint [--file <toml>]`은 구조를 따로 검사한다. 모든 종류의 상태가 정의돼 있는지, 검사가 가리키는 절과 항목이 계획 틀에 있는지, 조건 키와 선택이 맞는지, 보호 파일 목록을 줄이지 않았는지 본다.

## 구조 파일 형식

| 표 | 키 |
|---|---|
| `[kinds.<종류>]` | states(상태 순서), options(start --with로 켤 수 있는 선택), when(상태별 들를 조건), extra_sections(상태가 쓰지 않아도 틀에 넣을 절) |
| `[states.<상태>]` | todo, checks, sections(이 상태가 쓰는 절), code(true면 코드 편집과 커밋이 열린다), run(true면 gate run을 받는다), review(검토자 지시문) |
| `[sections."<절>"]` | body(계획 틀에 들어갈 표나 bullet) |

when의 조건 키는 option(켠 선택), changed(계획 시작 뒤 바뀐 파일 패턴), max_files(계획의 코드 파일 수 상한), not, any다. 예를 들어 `MIGRATE = {changed = ["migrations/*"]}`는 migrations/가 바뀐 계획만 MIGRATE에 들른다.

## 막는 장치

| 장치 | 막는 경우 |
|---|---|
| guard (PreToolUse) | 코드 상태인 계획이 없는데 code 파일을 고칠 때. 상태 파일, .git/, 구조 파일(config.toml, history, gatekit)을 직접 쓸 때. 상태를 gate 명령 말고 다른 방법으로 다룰 때. adopt의 승인 문장이 이번 턴 사용자 입력에 없을 때. hook 우회 |
| stop (Stop) | 이 세션이 고친 코드, 하네스, 상태 파일에 커밋하지 않은 변경이 있을 때(이번 턴에 고친 문서는 알리기만 한다). 이번 턴에 다룬 계획이 stop_states에 머물 때. 일반 질문이나 부가 작업 턴은 앞 턴의 계획 상태로 막지 않는다. 5번 연속 막으면 통과시킨다 |
| pre-commit | 커밋할 코드 파일이 코드 상태인 계획의 files_section 절에 없을 때 |
| commit-msg | 하네스 파일과 다른 파일이 한 커밋에 섞일 때. 하네스 커밋의 메시지가 `harness:`로 시작하지 않을 때 |

guard와 stop이 막은 기록은 git 폴더의 gate-events.jsonl에 남아 `diagnose`가 쓴다. 마지막 구조 변경 뒤 끝난 계획이나 막힌 횟수가 기준을 넘거나 프로젝트 단계가 바뀌면 SessionStart가 하네스 점검을 권한다.
