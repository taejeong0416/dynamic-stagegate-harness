# 작업 state machine

작업 하나는 계획 파일 하나와 정해진 상태 경로를 가진다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 `.gate/gate.py next`가 계획 파일, git 기록, 테스트나 eval 명령, 결과 파일로 검사한다. 판단이 필요한 상태는 `next --review`로 부른 검토자가 PASS를 줘야 넘어가고, 검토자는 읽기 도구만 쓰는 별도 Claude 호출이다.

종류, 상태 경로, 상태별 검사로 된 지금 구조는 `python -X utf8 .gate/gate.py describe`가 `.gate/config.toml`에서 바로 출력하고, `describe --mermaid`는 종류마다 경로를 Mermaid flowchart로 출력한다. 점선은 back으로 돌아가는 경로이고, 점선 상자로 묶인 상태들은 같은 상태로 돌아간다. 프로젝트의 목적과 검증 방법은 `.gate/PROFILE.md`에 있다.

## 작업 명령

| 명령 | 동작 |
|---|---|
| `start --kind <종류> --title "<제목>" [--with <선택>] [--from P001]` | 계획 파일을 만들고 첫 상태로 시작한다. `--with`는 종류의 options에 있는 선택 상태를 켠다 |
| `next P001 [--review]` | 지금 상태의 조건을 검사하고, 통과하면 조건이 맞는 다음 상태로 옮긴다. 조건이 맞지 않는 상태는 건너뛰고 기록한다 |
| `back P001 <상태> --why "..."` | 앞 상태로 돌아간다. 종류의 back에 지금 상태가 있으면 거기 적힌 상태로만 간다 |
| `run P001 --eta HH:MM --basis "..."` | 실행 상태에서 예상 종료 시각과 근거를 적는다 |
| `abandon P001 --why "..."` | 계획을 그만둔다 |
| `hold P001 --quote "..."` | 사용자가 이번 턴에 멈추라고 한 문장을 적는다. 그 문장이 든 턴에서는 stop hook이 이 계획으로 막지 않는다 |
| `stage <n> --decision "..."` | stages가 있는 구조에서 프로젝트 단계를 바꾼다 |
| `status` | 진행 중인 계획과 할 일을 출력한다 |

명령은 모두 `python -X utf8 .gate/gate.py` 뒤에 붙인다.

## 구조 바꾸기

구조는 프로젝트가 자라면서 바뀐다. 새 폴더나 산출물이 생기면 새 상태나 경로가 필요하고, 형식만 채우고 지나가는 상태는 빼거나 조건을 붙인다. 구조는 아래 절차로만 바꾼다.

1. `diagnose`로 상태별 기록, 상태별 검토 호출 수와 비용, 되돌아간 경로별 횟수와 이유, hook이 막은 기록, `next`가 통과하지 못한 이유별 횟수, 저장소 변화를 본다. 상태별 기록은 들어온 횟수, back, 건너뜀, 머문 시간, 절 글자 수, 검토 FAIL 이유다.
2. `.gate/proposals/H###.md`에 제안서를 쓴다. "바꾸는 것" 표에 항목마다 근거를 쓰고, "진행 중인 계획" 절에 영향을 쓰고, "새 구조" 절에 새 config 전체를 toml 블록으로 넣는다. version은 지금보다 1 크다.
3. `check-proposal H### --review`가 lint, 빼거나 푸는 변경 목록, 지난 계획 재생, diff를 `H###.diag.md`에 쓰고 검토자를 부른다. 목록에는 새 구조에서 빠지는 종류, 상태, 검사, 검토와 풀리는 편집 제한, 새로 붙거나 바뀌는 건너뛰기 조건, 빠지거나 바뀌는 상한이 나오고, 검토자는 그 가운데 "바꾸는 것" 표에 근거가 없는 변경을 찾는다.
4. 사용자에게 바꾸는 것과 근거를 보이고 승인을 받는다.
5. `adopt H### --approved "<사용자가 승인한 문장>"`으로 적용한다. guard가 그 문장이 이번 턴 사용자 입력에 있는지 본다. 앞 구조는 `.gate/history/v<N>.toml`에 남고, 진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다.

`lint [--file <toml>]`은 구조를 따로 검사한다. 모든 종류의 상태가 정의돼 있는지, 검사가 가리키는 절과 항목이 계획 틀에 있는지, 조건 키와 선택이 맞는지, 보호 파일 목록을 줄이지 않았는지 본다.

## 구조 파일 형식

| 표 | 키 |
|---|---|
| `[kinds.<종류>]` | states(상태 순서), options(start --with로 켤 수 있는 선택), when(상태별 들를 조건), back(상태별로 돌아갈 수 있는 앞 상태), limit(이 종류로 진행할 수 있는 조건), extra_sections(상태가 쓰지 않아도 틀에 넣을 절) |
| `[states.<상태>]` | todo, checks, sections(이 상태가 쓰는 절), code(true면 코드 편집과 커밋이 열린다), edit("tests"면 code 파일 가운데 `[project] tests` 패턴에 맞는 파일만 고치고 커밋한다), run(true면 gate run을 받는다), review(검토자 지시문), review_when(검토를 걸 종류), review_model(검토자 모델) |
| `[sections."<절>"]` | body(계획 틀에 들어갈 표나 bullet) |
| `[review]` | tools(검토자가 쓰는 도구), model(review_model이 없는 상태의 검토자 모델), effort(검토자 추론 단계. low, medium, high, xhigh, max), timeout(초) |

checks의 검사와 review_when에는 `only = [종류]`나 `skip = [종류]`를 붙여 적용할 종류를 고른다.

when의 조건 키는 option(켠 선택), changed(계획 시작 뒤 바뀐 파일 패턴), max_files(계획의 코드 파일 수 상한), not, any다. 예를 들어 `MIGRATE = {changed = ["migrations/*"]}`는 migrations/가 바뀐 계획만 MIGRATE에 들르게 한다.

back은 `back = {TEST = ["IMPLEMENT", "PLAN"]}`처럼 떠나는 상태마다 돌아갈 수 있는 앞 상태를 적는다. 적지 않은 상태에서는 앞 상태 어디로든 돌아가고, 빈 목록을 적은 상태에서는 돌아가지 못한다. 돌아가면 돌아간 상태와 그 뒤 상태를 넘긴 기록이 지워진다.

limit은 when과 같은 조건 키를 쓴다. `limit = {max_files = 5}`인 종류의 계획이 코드 파일을 6개 적으면 pre-commit과 `next`가 막고, 그 계획은 abandon한 뒤 맞는 종류로 `start --from`해서 잇는다.

unchanged 검사는 `next`가 상태를 넘길 때 남긴 절 내용 기록과 지금 계획 파일을 견준다. `{check = "unchanged", state = "SPEC"}`는 SPEC이 쓰는 절 전체를, `{check = "unchanged", state = "TESTFIRST", section = "테스트 계획", items = ["새 테스트 명령"]}`는 그 bullet 항목만 본다. 바뀐 내용이 맞으면 back으로 그 상태에 돌아가 다시 넘긴다.

test_edits 검사는 `[project] tests` 패턴에 맞는 계획의 테스트 파일 가운데 state를 처음 넘긴 뒤 고치거나 지운 파일이 section 표의 첫 칸에 있는지 본다. state를 주지 않으면 계획을 시작한 뒤를 보고, 새로 더한 테스트 파일은 보지 않는다. 테스트가 틀렸으면 고치고 표에 이유를 쓰며, 기대값을 구현에 맞춰 바꾸지 않는다.

covers 검사는 from 절 표의 첫 칸이 모두 section 절 표의 첫 칸에 있는지 본다. `{check = "covers", section = "테스트 결과", from = "수용 기준"}`이면 수용 기준마다 테스트 결과 행이 있어야 넘어가고, 기준 문장은 공백만 다르면 같은 것으로 본다.

command 검사의 `use`는 `[project]`의 test나 affected를 가리키고, affected가 비어 있으면 test를 쓴다. 명령 속 `{files}`는 계획의 코드 파일로 바뀌고, `optional = true`면 명령이 없을 때 통과한다. `expect = "fail"`이면 테스트가 돌고 실패해야 한다. 명령의 프로그램을 찾지 못하거나 종료 코드가 `reject`에 있으면 막는데, `reject`의 기본값은 4, 5, 126, 127이다. 같은 명령과 같은 테스트 파일로 실패를 확인한 기록이 있으면 다시 돌리지 않는다. 그래서 구현한 뒤 back으로 TESTFIRST나 REPRO에 돌아와도 테스트를 고치지 않았다면 다시 넘길 수 있다.

검토자는 사용자 설정, MCP 서버, skill을 읽지 않는 `claude -p` 호출이다. 사용자 설정의 추론 단계도 따르지 않아서 검토자의 추론 단계는 `[review] effort`로 정한다. 호출마다 횟수와 비용이 그 상태의 검토 기록에 쌓이고 `diagnose`의 검토 호출 표에 나온다.

review 지시문에는 `{plan}`, `{title}`, `{kind}`, `{state}`, `{diff}`를 쓸 수 있다. `{diff}`는 계획을 시작한 뒤의 코드 diff를 담아 git 폴더의 gate-review/에 둔 파일의 경로로 바뀌고, 코드가 바뀌면 계획 내용이 같아도 다시 검토한다.

## 막는 장치

| 장치 | 막는 경우 |
|---|---|
| guard (PreToolUse) | 코드 상태인 계획이 없는데 code 파일을 고칠 때. 코드 상태인 계획이 모두 edit = "tests" 상태인데 테스트가 아닌 code 파일을 고칠 때. 상태 파일, .git/, 구조 파일(config.toml, history, gatekit)과 git hook을 직접 쓸 때. 상태를 gate 명령 말고 다른 방법으로 다룰 때. adopt의 승인 문장이 이번 턴 사용자 입력에 없을 때. hook을 우회할 때 |
| stop (Stop) | 이 세션이 고친 코드, 하네스, 상태 파일에 커밋하지 않은 변경이 있을 때. 이번 턴에 고친 문서는 알리기만 한다. 이번 턴에 다룬 계획이 stop_states에 머물 때. 일반 질문이나 부가 작업 턴은 앞 턴의 계획 상태로 막지 않는다. 5번 연속 막으면 통과시킨다 |
| pre-commit | 커밋할 코드 파일이 코드 상태인 계획의 files_section 절에 없을 때. 그 파일을 적은 계획이 edit = "tests" 상태인데 테스트 파일이 아닐 때. 그 파일을 적은 계획이 종류의 limit을 벗어났을 때 |
| commit-msg | 하네스 파일과 다른 파일이 한 커밋에 섞일 때. 하네스 커밋의 메시지가 `harness:`로 시작하지 않을 때 |

guard와 stop이 막은 기록과 `next`가 통과하지 못한 이유는 git 폴더의 gate-events.jsonl에 남아 `diagnose`가 쓴다. 마지막 구조 변경 뒤 끝난 계획이나 guard와 stop이 막은 횟수가 기준을 넘거나 프로젝트 단계가 바뀌면 SessionStart가 하네스 점검을 권한다.
