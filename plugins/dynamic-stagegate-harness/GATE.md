# 작업 state machine

작업 하나는 계획 파일 하나와 정해진 상태 경로를 가진다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 `.gate/gate.py next`가 계획 파일, git 기록, 테스트나 eval 명령, 결과 파일로 검사한다. 판단이 필요한 상태는 `next --review`로 부른 검토자가 PASS를 줘야 넘어가고, 검토자는 읽기 도구만 쓰는 별도 Claude 호출이다.

종류, 상태 경로, 상태별 검사로 된 지금 구조는 `python -X utf8 .gate/gate.py describe`가 `.gate/config.toml`에서 바로 출력하고, `describe --mermaid`는 종류마다 경로를 Mermaid flowchart로 출력한다. 점선은 back으로 돌아가는 경로이고, 점선 상자로 묶인 상태들은 같은 상태로 돌아간다. 프로젝트의 목적과 검증 방법은 `.gate/PROFILE.md`에 있다.

## 작업 명령

| 명령 | 동작 |
|---|---|
| `start --kind <종류> --title "<제목>" [--with <선택>] [--from P001]` | 계획 파일을 만들고 첫 상태로 시작한다. `--with`는 종류의 options에 있는 선택 상태를 켠다 |
| `next P001 [--review]` | 지금 상태의 조건을 검사하고, 통과하면 조건이 맞는 다음 상태로 옮긴다. 조건이 맞지 않는 상태는 건너뛰고 기록한다 |
| `back P001 <상태> --why "..." --defect <분류>` | 앞 상태로 돌아간다. 종류의 back에 지금 상태가 있으면 거기 적힌 상태로만 간다. 되돌아가는 원인의 분류(logic 코드 논리, data 데이터 형태나 누수, resource 시간과 메모리와 디스크, env 실행 환경, plan 계획이나 판정 기준)를 받아 사고로 남긴다 |
| `incident --defect <분류> --note "..." [--plan P001] [--state <상태>] [--quote "..."]` | 통과한 뒤에 드러난 결함을 사고로 남긴다. 결과가 무효였거나 사용자가 결함을 지적했을 때 쓴다. `--quote`는 사용자가 이번 턴에 쓴 문장이어야 한다 |
| `run P001 --eta HH:MM --basis "..."` | 실행 상태에서 예상 종료 시각과 근거를 적는다. 이때 이미 넘긴 상태의 절 기록을 고정하고, 상태 파일만 `docs: P001 실행 기록`으로 바로 커밋한다. 실행 중에 예상이 바뀌면 다시 적어 시각을 고친다. 실행 기록은 상태를 옮기면 계획의 runs 이력으로 넘어가 status와 검사에서 빠진다. 같은 실행 상태에 다시 들어오면 새로 적어야 한다 |
| `abandon P001 --why "..."` | 계획을 그만둔다 |
| `hold P001 --quote "..."` | 사용자가 이번 턴에 멈추라고 한 문장을 적는다. 그 문장이 든 턴에서는 stop hook이 이 계획으로 막지 않는다 |
| `stage <n> --decision "..."` | stages가 있는 구조에서 프로젝트 단계를 바꾼다 |
| `status [--line]` | 진행 중인 계획과 할 일, 종류의 상태 경로를 출력한다. 경로에는 지난 상태를 ✓, 건너뛴 상태를 건너뜀, 지금 상태를 [ ]로 표시하고 두 번째 방문부터 회차를 붙인다. `--line`은 계획마다 번호, 종류, 상태 경로, 실행 기록에 적은 예상 종료 시각을 statusline용으로 한 줄씩 출력하고, stages가 있으면 첫 줄 앞에 프로젝트 단계를 붙인다. Claude Code statusline에 띄우려면 플러그인의 `scripts/statusline.py --install`로 켜는데, 켤지는 setup skill이 묻는다 |
| `records` | 현재 상태 문서, 보관 문서, 상태 파일의 크기와 정리를 권하는 이유를 출력한다 |

명령은 모두 `python -X utf8 .gate/gate.py` 뒤에 붙인다.

## 구조 바꾸기

구조는 프로젝트가 자라면서 바뀐다. 새 폴더나 산출물이 생기면 새 상태나 경로가 필요하고, 형식만 채우고 지나가는 상태는 빼거나 조건을 붙인다. 구조는 아래 절차로만 바꾼다.

1. `diagnose`로 상태별 기록, 상태별 검토 호출 수와 비용, 되돌아간 경로별 횟수와 이유, hook이 막은 기록, `next`가 통과하지 못한 이유별 횟수, 저장소 변화, 부품 상태, 사고 기록, 부품 성적, 제안 초안, 기록 크기를 본다. 상태별 기록은 들어온 횟수, back, 건너뜀, 머문 시간, 절 글자 수, 검토 FAIL 이유다.
2. `propose H###`로 지금 부품(`.gate/parts`)과 `.gate/local.toml`의 사본을 `.gate/proposals/H###/`에 만들고, 그 사본에서 부품을 더하고 빼고 고친다. 라이브러리 부품은 `.gate/library/<이름>` 폴더를 사본의 parts 아래에 복사해서 더한다. `.gate/proposals/H###.md`에는 "바꾸는 것" 표에 항목마다 근거를 쓰고 "진행 중인 계획" 절에 영향을 쓴다. parts 폴더가 없는 프로젝트는 사본 대신 제안서의 "새 구조" 절에 새 config 전체를 toml 블록으로 넣고 version을 지금보다 1 크게 쓴다.
3. `check-proposal H### --review`가 사본을 조립해서 부품별 변경(더함, 뺌, 고침), lint, 빼거나 푸는 변경 목록, 지난 계획 재생, 지난 계획 검사 재생, diff를 `H###.diag.md`에 쓰고 검토자를 부른다. 검사 재생은 끝난 계획의 계획 파일에 새 구조의 검사를 다시 돌려서, 새로 막히는 계획과 더는 막히지 않는 계획을 보인다. 계획 파일만 보고 판정하는 검사(filled, table, covers, cell_max, choice, refs, paths_exist)만 돌린다. 검토 뒤에 제안서나 사본이 바뀌면 다시 검토받아야 한다. 목록에는 새 구조에서 빠지는 종류, 상태, 검사, 검토와 풀리는 편집 제한, 새로 붙거나 바뀌는 건너뛰기 조건, 빠지거나 바뀌는 상한, 낮아지는 검토 모델, 생기거나 바뀌는 검토 면제 항목이 나온다. [project], [review], [remote], [[stages]]도 키마다 푸는 방향으로 본다. code, tracked, stop_states, tests 같은 목록은 원소가 빠지면, unreviewed, exec_states, 검토 tools, 단계별 허용 종류는 원소가 늘면, max_visits 같은 상한은 커지면, 검토 model과 effort는 낮아지면 푸는 변경이다. 방향을 정할 수 없는 키가 바뀌면 "바뀐 설정"으로 올린다. 검토자는 목록 가운데 "바꾸는 것" 표에 근거가 없는 변경과, "바뀐 설정" 가운데 실제로 검사를 푸는 변경을 찾는다.
4. 승인 방식은 [project] approval이 정한다. 기본값 auto에서는 check-proposal을 통과한 제안서를 묻지 않고 적용하고, 바꾼 것과 근거를 결정 기록에 쓰고 답에 보고한다. user이면 사용자에게 바꾸는 것과 근거를 보이고 승인을 받는다.
5. `adopt H###`로 적용한다. approval이 user이면 `adopt H### --approved "<사용자가 승인한 문장>"`으로 쓰고, guard가 그 문장이 이번 턴 사용자 입력에 있는지 본다. adopt는 parts 폴더, local.toml, config.toml, parts.lock을 사본의 것으로 바꾼다. 앞 구조는 `.gate/history/v<N>.toml`에 남고, 진행 중인 계획은 시작할 때의 버전을 끝까지 쓴다. 종류와 상태뿐 아니라 [project]의 계획 값(code, tests, files_section, max_visits, stop_states, test, affected, unreviewed), [review], [remote] exec_states도 계획 버전을 따른다. guard와 pre-commit처럼 여러 계획을 합치는 판단은 지금 구조나 진행 중인 계획의 버전 가운데 하나라도 코드로 보는 파일을 코드로 보고, 허용할 파일은 계획마다 자기 버전으로 뽑는다. 저장소에 하나뿐인 값(plans_dir, state_file, plan_prefix, harness, protected, tracked, pre_commit, status와 decisions 파일, 서버 주소)은 지금 구조를 따른다.

계획을 시작할 때 엔진(.gate/gatekit) 내용의 해시를 남긴다. 엔진이 바뀐 진행 중 계획은 status에 "엔진 바뀜", SessionStart에 "[엔진 변경]"으로 나오고, setup update는 진행 중인 계획이 있으면 경고한다. 엔진의 앞 버전은 보관하지 않으므로, 진행 중인 계획을 끝낸 뒤 갱신하는 편이 안전하다. 엔진은 설치한 프로젝트의 .gate/gatekit에서 고치지 않는다. 플러그인 저장소에서 고쳐 그 저장소의 tests/로 시험한 뒤 setup update로 받는다.

`lint [--file <toml>]`은 구조를 따로 검사한다. 모든 종류의 상태가 정의돼 있는지, 검사가 가리키는 절과 항목이 계획 틀에 있는지, 조건 키와 선택이 맞는지, 보호 파일 목록을 줄이지 않았는지 본다. `--file` 없이 부르면 config.toml이 부품과 local.toml을 조립한 결과와 같은지, 부품 파일이 parts.lock과 같은지도 본다.

## 부품

구조는 `.gate/parts/<이름>/part.toml` 부품들과 `.gate/local.toml`을 조립한 결과이고, 조립 결과가 `.gate/config.toml`이다. 엔진은 config.toml만 읽는다. `parts`는 쓰는 부품과 쓰지 않는 라이브러리 부품(`.gate/library/`)을 출력하고, 적용 조건을 지금 저장소와 답으로 다시 판정해서 조건이 거짓이 된 부품과 조건이 맞게 된 부품을 알린다. 프로젝트에서 고친 부품과 라이브러리에 새 버전이 있는 부품도 표시한다.

| part.toml 키 | 뜻 |
|---|---|
| name, description, version, origin | 이름, 설명, 버전, 출처(library나 project) |
| applies_when | 적용 조건. file(glob에 맞는 파일이 있다), command(명령이 성공한다), answer(고정 질문의 답), not, any, all |
| requires, conflicts | 함께 있어야 하는 부품과 같이 둘 수 없는 부품 |
| covers | 이 부품이 잡으려는 결함 분류 |
| `[project]`, `[remote]`, `[review]`, `[[stages]]` | 구조에 더하는 설정. 목록은 합치고, 값 하나짜리 키를 두 부품이 다르게 정하면 조립 문제다 |
| `[kinds]`, `[states]`, `[sections]` | 종류, 상태, 절의 정의. 상태의 roles에 역할(plan, implement, execute, verify, record, decide)을 적는다 |
| `[[attach]]` | roles나 states로 고른 상태에 더하는 sections, todo, checks와 그 밖의 상태 키. at으로 순서를 정한다(기본 50, 상태 정의도 50) |
| `[[insert]]` | state를 before나 after에 적은 역할의 앞이나 뒤에 넣는다. 뒤에 넣은 상태는 앞 상태의 back을 물려받는다 |
| `[[guard]]` | pattern(정규식)에 맞는 셸 명령을 reason을 보이며 막는다 |
| `[records]` | 기록 설정(아래 "기록") |
| provenance | project 부품을 만든 계기(사고 번호, 날짜, 증거 경로) |

attach와 insert의 only, skip은 종류 이름이다. 부품은 경로 부품(종류를 정하는 부품), library 부품, project 부품 순으로, 같은 묶음 안에서는 이름 순으로 쌓인다. 두 부품이 같은 종류, 상태, 절을 다르게 정의하면 조립 문제다.

local.toml의 `[answers]`는 고정 질문의 답(work, run, ships, tests, failure_cost)이고 부품을 고르는 데만 쓴다. `[project]`, `[remote]`, `[review]`는 키 단위로, `[kinds]`, `[states]`, `[sections]`는 이름 아래 필드 단위로 조립 결과를 덮어쓴다.

parts.lock에는 부품마다 버전, 출처, 파일 해시(hash), 라이브러리에서 복사할 때의 해시(from)가 있다. hash와 from이 같으면 프로젝트에서 고치지 않은 부품이다.

부품 폴더에 `checks.py`를 두면 엔진이 읽어 검사를 더한다. 모듈은 `from gatekit.checks import check`로 받은 `@check`를 함수에 붙이고, 함수는 `(ctx, spec)`을 받아 빠진 항목 문장 목록을 돌려준다. 조립한 config.toml의 검사에는 `part = "<부품>"`이, review가 있는 상태에는 `review_part`가 붙는다. 막힌 기록과 검토 비용을 부품별로 모으는 데 쓴다.

## 사고와 성적

게이트는 사고에서 만든다. 사고는 `back --defect`와 `incident`로 상태 파일에 남고, 막힌 기록은 git 폴더의 gate-events.jsonl에 구조 버전, 엔진 해시, 막은 검사의 부품과 함께 남는다.

막힌 기록에는 판정이 붙는다. 막힌 뒤 구조 버전과 엔진이 그대로인 채 같은 행동이 통과했으면 작업을 고쳐서 넘어간 것이라 진짜 막힘이다. 구조나 엔진이 바뀐 뒤에 통과했으면 하네스를 고쳐서 넘어간 것이라 오탐이다. 다시 통과한 기록이 없으면 미판정이다. `next`가 막은 기록은 그 계획이 같은 상태를 넘긴 기록과, guard가 막은 기록은 같은 파일 편집이나 같은 명령이 통과한 기록과 짝짓는다.

`diagnose`의 "부품 성적" 표는 부품마다 막은 횟수, 진짜 막힘, 오탐, 미판정, covers에 해당하는 분류의 사고, 검토 호출 수와 비용, 검토 지적 가운데 결함 지적과 형식 지적의 수를 보인다. 부품이 없는 guard와 stop의 막힘은 "엔진" 행에 모인다. "제안 초안"은 아래 기준으로 나온다. 기준값은 근거 없이 정한 값이고 `[project]`의 같은 이름 키로 바꾼다.

| 초안 | 조건 | 기준 키(기본값) |
|---|---|---|
| 생성 | 같은 결함 분류의 사고가 서로 다른 계획에서 나왔는데 covers가 그 분류인 부품이 없다 | gate_new_plans(2) |
| 강화 | 쓰는 부품의 covers에 해당하는 분류의 사고가 나왔다 | gate_leaks(1) |
| 완화 | 최근 계획 동안 그 부품의 오탐이 기준 이상이고 진짜 막힘이 없다 | gate_relax_false(3), gate_relax_window(10) |
| 올리기 | project 부품의 진짜 막힘이 기준 이상이다 | gate_promote_true(3) |

초안은 판단 자료다. 생성 초안을 따를 때는 그 사고를 막는 검사를 project 부품으로 쓰고, check-proposal의 검사 재생에서 사고가 난 계획이 새로 막히는지 확인한다.

## 기록

기록이 쌓여도 한 번에 읽는 양이 늘지 않게 한다. `[records]`의 키는 아래와 같다.

| 키 | 뜻 | 기본값 |
|---|---|---|
| current_max | 현재 상태 문서(`[project] status_file`)의 글자 수 상한 | 10000 |
| archive_file | 현재 상태 문서에서 닫힌 항목을 옮기는 보관 문서 | 없음 |
| index | 계획 색인 파일. 계획이 닫힐 때마다 엔진이 다시 쓴다 | 없음 |
| index_items | 색인에 옮길 계획 파일 항목. "절: 항목" 꼴 | 없음 |
| ledgers | 덧붙이기만 하는 기록(실험 기록, 결정 문서). 참조가 풀리는지 볼 때와 발췌에서 대체된 항목을 가릴 때 쓴다 | 없음 |
| supersedes | 원장의 새 항목이 옛 항목을 가리키는 줄의 정규식. 그 줄의 따옴표 속 문구가 든 앞쪽 제목이 대체된 것으로 본다. 예: `^- 대체하는 결정:` | 없음 |
| superseded | 원장의 옛 항목이 스스로 대체됐다고 적는 줄의 정규식. 예: `^- 대체됨:` | 없음 |
| context_recent | `{context}` 발췌에 제목과 첫 줄을 넣는 최근 제목 수. 인용되지 않은 그 앞 제목은 뺀다. 0이면 모두 넣는다 | 30 |
| item_id | 현재 상태 문서 항목의 고정 ID 정규식. 예: `\\[(S\\d{3})\\]` | 없음 |
| keep_closed | state.json에 남겨 둘 닫힌 계획 수. 넘으면 오래된 것부터 state-archive.jsonl로 옮긴다 | 10 |
| events_keep | 막힌 기록에 남겨 둘 줄 수. 넘으면 diagnose가 오래된 줄을 scorecard.json으로 접는다 | 500 |
| cleanup_every | 마지막 정리 뒤 이만큼 계획이 닫히면 정리를 권한다 | 10 |

계획 파일은 닫혀도 자리를 바꾸지 않고, 색인에 계획마다 한 줄(번호, 제목, 종류, 결과, 닫은 날, index_items의 값)이 남는다. 보관 파일로 옮긴 계획도 `start --from`, `diagnose`, 재생에서 그대로 쓰인다. state-archive.jsonl과 scorecard.json은 gate 명령만 쓴다.

검사가 읽어야 하는 사실은 현재 상태 문서 머리의 필드에 둔다. 문서 첫 줄을 `---`로 시작하고 다음 `---`까지 `이름: 값` 줄을 쓴다. SessionStart는 이 필드를 한 줄로 넣는다.

| 검사 | 통과 조건 |
|---|---|
| doc_size | 현재 상태 문서(path로 다른 문서)가 current_max(max로 다른 값) 글자 안이다 |
| field | 문서 머리의 field에 값이 있다. `after_last = {file, pattern}`을 주면 그 값의 번호가 file에서 pattern에 맞는 가장 큰 번호의 다음이다 |
| survey | 정리 전의 문서 크기, 항목 ID, 풀리는 참조를 계획 기록에 남긴다 |
| kept | survey 때 풀리던 참조가 지금도 풀리고, 그때 있던 항목 ID가 현재 상태 문서나 보관 문서에 있다 |

참조는 현재 상태 문서와 보관 문서가 인용한 계획 번호와 `[review] cite` 패턴이다. 계획 번호는 계획 기록이나 계획 파일이 있으면, 그 밖의 참조는 ledgers 문서에 있으면 풀린다.

현재 상태 문서가 상한을 넘거나 마지막 정리 뒤 닫힌 계획이 cleanup_every개가 되면 SessionStart가 기록 정리를 권한다. 정리 경로 부품(cleanup)의 tidy 종류는 SURVEY에서 정리 전 상태를 재고, TIDY에서 닫힌 항목을 보관 문서로 옮기고, CONFIRM에서 kept와 doc_size로 확인한다. 종류에 `any_stage = true`를 두면 프로젝트 단계와 관계없이 시작할 수 있다.

## 구조 파일 형식

| 표 | 키 |
|---|---|
| `[kinds.<종류>]` | states(상태 순서), options(start --with로 켤 수 있는 선택), when(상태별 들를 조건), back(상태별로 돌아갈 수 있는 앞 상태), limit(이 종류로 진행할 수 있는 조건), extra_sections(상태가 쓰지 않아도 틀에 넣을 절) |
| `[states.<상태>]` | todo, checks, sections(이 상태가 쓰는 절), code(true면 코드 편집과 커밋이 열린다), edit("tests"면 code 파일 가운데 `[project] tests` 패턴에 맞는 파일만 고치고 커밋한다), run(true면 gate run을 받는다), review(검토자 지시문), review_when(검토를 걸 종류), review_model(검토자 모델), context(`{context}` 발췌에 담을 파일 목록), review_exempt(`{section, item}`. 그 bullet 항목이 "예"로 시작하면 검토자를 부르지 않는다) |
| `[sections."<절>"]` | body(계획 틀에 들어갈 표나 bullet) |
| `[review]` | tools(검토자가 쓰는 도구), model(review_model이 없는 상태의 검토자 모델), effort(검토자 추론 단계. low, medium, high, xhigh, max), timeout(초), cite(`{context}` 발췌에 담을 참조의 정규식 목록. 기본은 계획 번호 꼴), context_full(이 글자 수 이하인 파일은 발췌하지 않고 전문을 담는다. 기본 6000), formats_block(false면 [형식] 지적만 남은 FAIL은 막지 않고 알리기만 한다. 기본 true) |
| `[remote]` | host, port, dir(학습 서버), exec_states(gate run 기록이 없어도 ssh로 저장소 코드를 그 자리에서 돌려도 되는 상태), sample_states와 sample_pattern(그 상태에서는 명령에 sample_pattern에 맞는 표본 경로가 들어간 ssh 실행만 연다. experiment 부품은 IMPLEMENT와 results/P###_sample) |
| `[[stages]]` | n(단계 번호), name, kinds(그 단계에서 시작할 수 있는 종류), exit(다음 단계로 넘어갈 조건 검사 목록), require(그 단계에서 새 계획을 시작하기 전에 통과해야 하는 검사 목록) |

checks의 검사와 review_when에는 `only = [종류]`나 `skip = [종류]`를 붙여 적용할 종류를 고른다.

단계의 exit와 require에는 계획 파일 없이 도는 검사만 쓴다. 기본 검사로는 field, doc_size, recent_closed가 있고, 프로젝트 고유의 조건은 부품 checks.py의 검사로 쓴다. 지금 단계의 exit 검사가 모두 통과하면 SessionStart와 `start`가 "[단계 전환 검토]"를 띄우고, 에이전트는 다음 단계로 넘어갈지 사용자에게 묻는다. 단계를 바꾸지는 않고 막지도 않는다. require 검사가 통과하지 않으면 `start`가 그 단계의 새 계획을 막고, `any_stage = true`인 종류는 막지 않는다. recent_closed는 이번 단계에서 닫힌 계획 가운데 최근 n개(기본 1)가 모두 조건에 맞는지 본다. kind로 종류를, states(기본 DONE)로 닫힌 상태를 고르고, item("절: 항목")과 pattern을 주면 그 항목 값이 정규식에 맞아야 한다. 예를 들어 `exit = [{check = "recent_closed", n = 3, kind = "tune", item = "결정: 고른 갈래", pattern = "기각"}]`이면 tune 계획이 세 번 연달아 기각으로 끝났을 때 전환 검토를 띄운다.

when의 조건 키는 option(켠 선택), changed(계획 시작 뒤 바뀐 파일 패턴), max_files(계획의 코드 파일 수 상한), not, any다. 예를 들어 `MIGRATE = {changed = ["migrations/*"]}`는 migrations/가 바뀐 계획만 MIGRATE에 들르게 한다.

back은 `back = {TEST = ["IMPLEMENT", "PLAN"]}`처럼 떠나는 상태마다 돌아갈 수 있는 앞 상태를 적는다. 적지 않은 상태에서는 앞 상태 어디로든 돌아가고, 빈 목록을 적은 상태에서는 돌아가지 못한다. 돌아가면 돌아간 상태와 그 뒤 상태를 넘긴 기록이 지워진다.

limit은 when과 같은 조건 키를 쓴다. `limit = {max_files = 5}`인 종류의 계획이 코드 파일을 6개 적으면 pre-commit과 `next`가 막고, 그 계획은 abandon한 뒤 맞는 종류로 `start --from`해서 잇는다.

unchanged 검사는 `next`가 상태를 넘길 때 남긴 절 내용 기록과 지금 계획 파일을 견준다. `{check = "unchanged", state = "SPEC"}`는 SPEC이 쓰는 절 전체를, `{check = "unchanged", state = "TESTFIRST", section = "테스트 계획", items = ["새 테스트 명령"]}`는 그 bullet 항목만 본다. 바뀐 내용이 맞으면 back으로 그 상태에 돌아가 다시 넘긴다. 다만 `gate run`을 적었거나 실행 상태에서 결과 파일이 생긴 뒤에는 결과가 보이므로, 그때까지 넘긴 상태의 기록을 고정한다. back으로 다시 넘겨도 기준이 바뀌지 않아 결과를 본 뒤 판정 경계나 eval 케이스를 고쳐 통과할 수 없고, 새 기준이 필요하면 `start --from`으로 새 계획을 잇는다.

code_unchanged 검사는 이 상태에 들어온 뒤 계획의 코드 파일에 커밋이나 커밋하지 않은 변경이 없는지 본다. 실행 상태에 붙이면 결과 파일이 앞에서 검사받은 코드에서 나왔는지 확인할 수 있고, 바뀌었으면 코드 상태로 돌아가 다시 지난 뒤 다시 실행한다.

test_edits 검사는 `[project] tests` 패턴에 맞는 계획의 테스트 파일 가운데 state를 처음 넘긴 뒤 고치거나 지운 파일이 section 표의 첫 칸에 있는지 본다. state를 주지 않으면 계획을 시작한 뒤를 보고, 새로 더한 테스트 파일은 보지 않는다. 테스트가 틀렸으면 고치고 표에 이유를 쓰며, 기대값을 구현에 맞춰 바꾸지 않는다.

cell_max 검사는 section 절 표의 column번째 칸(0부터, 기본은 마지막 칸)이 행마다 max자 이하인지 본다. 검토자와 에이전트가 계획을 읽을 때마다 긴 칸이 비용이 되기 때문이고, 실험 경로는 후보 표의 이유 칸을 한두 문장과 실험 번호로 쓰게 IDEATE와 SELECT에서 250자로 묶는다.

results 검사는 section 절의 item 칸(기본 "실행 계획: 결과 파일")에서 /가 들었거나 확장자가 있는 토큰만 경로로 고른다. 바로 뒤에 `<이름>`이나 `{k}` 같은 틀 문자가 붙은 경로는 실제 파일 이름이 아니라서 확인하지 않고, 서버 확인 셸에는 경로를 따옴표로 묶어 넘긴다. 계획이 일찍 끝나는 갈래(통과한 변형이 없거나 1차 검사에서 떨어짐)는 뒤 단계 파일을 만들지 않으므로, early 칸(기본 "조기 종료 결과 파일")의 파일이 모두 이 상태에 들어온 뒤에 생겼으면 나머지 결과 파일이 없어도 통과하고 그 사실을 출력한다. 서버 확인이 실패하면 조기 종료 파일이 있어도 막는다.

reviewed 검사와 검토자 호출은 검토한 계획 내용을 해시로 견준다. unreviewed 항목은 빼는데, "절"은 절 전체, "절: 항목"은 그 절의 "- 항목:" 줄, "절[n]"은 그 절 표의 n번째 칸이다(experiment 부품은 표본 실행 뒤 다시 고치는 "실행 계획: 예상 소요 시간과 근거"와 구현하며 이름이 바뀌는 "행 조건 대조표[3]"를 뺀다). 판정 경계 절(구조의 unchanged 검사가 고정하는 절)은 글자 그대로 묶으며, 나머지 절은 숫자를 지운 뒤 묶는다. 실측해 적은 묶음 수나 메모리 하나 바뀐 것으로 재검토가 걸리지 않게 하기 위해서고, 영문자 바로 뒤의 숫자(E085, P001 같은 번호)는 지우지 않는다. 첫 검토는 빠진 것을 한 번에 모두 내게 하고, 같은 상태의 재검토에는 앞 검토 지적을 넘겨 그 지적이 고쳐졌는지와 고치며 바뀐 내용만 보게 한다. 앞 검토 때 이미 있던 내용에는 누수, 판정 기준이나 결과별 다음 행동의 오류, 근거 없는 값처럼 결과 해석을 바꿀 결함만 새로 낸다. 검토를 통과하면 그때의 계획 원문을 git 폴더의 `gate-review/P###-<상태>-passed.md`에 남긴다. 통과한 계획을 고쳐 다시 검토받으면 그 원문과 비교한 diff를 `gate-review/P###-<상태>-change.diff`로 넘긴다. 검토자에게는 바뀐 내용과 그 변경 때문에 다른 절과 맞지 않게 된 곳만 보게 한다. change.diff는 {diff}로 넘기는 코드 diff와 따로 있다. 원문이 없는 옛 기록과 면제로 넘긴 기록은 첫 검토로 간다.

상태에 review_exempt를 두면 그 항목이 "예"로 시작하는 계획은 검토자를 부르지 않고 넘어간다. 실험 경로는 IMPLEMENT 코드 diff 검토에 `{section = "실행 계획", item = "가벼운 실행"}`을 두어, 서버 비용이 작은 실행은 diff 검토 없이 돌린다. 면제는 상태 파일의 그 상태 검토 기록에 exempt와 함께 통과로 남고 diagnose 검토 호출 표에는 모델 "면제"로 나온다. 그 칸이 맞는지는 PLAN 검토자가 본다. 면제로 넘긴 계획에서 `start --from`으로 이은 계획은 같은 상태의 diff 검토에서 부모 계획이 그 상태에서 바꾼 코드의 diff도 함께 받는다. 가벼운 실행의 결과가 이어진 계획의 무거운 실행에 쓰일 때 그 코드도 한 번은 검토받게 하기 위해서다.

covers 검사는 from 절 표의 첫 칸이 모두 section 절 표의 첫 칸에 있는지 본다. `{check = "covers", section = "테스트 결과", from = "수용 기준"}`이면 수용 기준마다 테스트 결과 행이 있어야 넘어가고, 기준 문장은 공백만 다르면 같은 것으로 본다.

command 검사의 `use`는 `[project]`의 test나 affected를 가리키고, affected가 비어 있으면 test를 쓴다. 명령 속 `{files}`는 계획의 코드 파일로 바뀌고, `optional = true`면 명령이 없을 때 통과한다. `expect = "fail"`이면 테스트가 돌고 실패해야 한다. 명령의 프로그램을 찾지 못하거나 종료 코드가 `reject`에 있으면 막는데, `reject`의 기본값은 4, 5, 126, 127이다. 같은 명령과 같은 테스트 파일로 실패를 확인한 기록이 있으면 다시 돌리지 않는다. 그래서 구현한 뒤 back으로 TESTFIRST나 REPRO에 돌아와도 테스트를 고치지 않았다면 다시 넘길 수 있다.

검토자는 사용자 설정, MCP 서버, skill을 읽지 않는 `claude -p` 호출이다. 사용자 설정의 추론 단계도 따르지 않아서 검토자의 추론 단계는 `[review] effort`로 정한다. 호출마다 횟수와 비용이 그 상태의 검토 기록에 쌓이고 `diagnose`의 검토 호출 표에 나온다. 검토자는 지적마다 앞에 [결함]이나 [형식]을 붙인다. [결함]은 고치지 않으면 실행 결과, 판정, 비교가 틀려지는 지적이고, [형식]은 기록 형식, 문구, 출처 표기에 관한 지적이다. 두 표시의 수는 검토 기록의 defects와 formats에 쌓여 부품 성적에 나온다. 형식 지적만 남은 FAIL도 기본으로는 막고, `[review] formats_block = false`면 넘기면서 그 지적을 출력한다.

review 지시문에는 `{plan}`, `{title}`, `{kind}`, `{state}`, `{diff}`, `{context}`를 쓸 수 있다. `{diff}`는 코드 diff를 담아 git 폴더의 gate-review/에 둔 파일의 경로로 바뀐다. 처음에는 계획을 시작한 뒤의 diff 전체를 주고, 같은 계획 내용으로 통과한 뒤에는 그 뒤에 바뀐 코드만 준다. 통과한 뒤 코드가 그대로면 다시 부르지 않고, 계획 내용이 바뀌면 diff 전체를 다시 본다.

`{context}`는 상태의 context에 적은 파일에서 뽑은 발췌 파일의 경로로 바뀐다. context_full자 이하인 파일은 전문을 담고, 넘는 파일은 계획 파일이 인용한 참조(cite 패턴과 계획 번호)가 든 줄, csv의 머리 행, 최근 `[records] context_recent`개 제목과 제목마다 첫 줄만 담는다. ledgers에 든 원장에서 supersedes나 superseded로 대체된 항목은 제목과 대체한 항목만 담는다. 결정 기록이나 실험 기록처럼 계속 커지는 문서를 검토자가 호출마다 통째로 읽지 않고, 기록이 쌓여도 발췌가 인용한 양만큼만 커지게 하기 위해서다. 지시문에는 발췌를 먼저 읽고 더 필요한 내용은 원본을 Grep으로 찾으라고 쓴다.

## 막는 장치

| 장치 | 막는 경우 |
|---|---|
| guard (PreToolUse) | code 파일이 코드 상태인 계획의 files_section 절에 없는데 고칠 때(pre-commit과 같은 기준). 코드 상태인 계획이 모두 edit = "tests" 상태인데 테스트가 아닌 code 파일을 고칠 때. 상태 파일, .git/, 구조 파일(config.toml, history, gatekit, parts, local.toml, parts.lock)과 git hook을 직접 쓸 때. 구조 파일이나 그 파일이 든 폴더(.gate/gatekit, .gate)가 나오는 셸 명령이 쓰기, 복사, 이동, 삭제 명령이나 인터프리터를 쓸 때(gate 명령과 읽기 명령만 이은 명령은 통과). 상태를 gate 명령과 읽기 명령 말고 다른 방법으로 다룰 때. glob으로 쓴 경로는 저장소에서 펼쳐 상태 파일과 구조 파일을 찾는다. adopt의 승인 문장이나 incident의 --quote 문장이 이번 턴 사용자 입력에 없을 때(다른 명령과 이어도 본다). 부품의 `[[guard]]` pattern에 맞는 명령일 때. hook을 우회할 때. [remote]가 있으면 커밋하지 않은 코드나 gate run 기록 없이 ssh로 작업을 띄우거나 코드를 올릴 때, ssh로 저장소 code 파일을 그 자리에서 돌리는데 gate run 기록이 있는 실행 상태나 exec_states 상태인 계획이 없고 sample_states 상태의 표본 경로 명령도 아닐 때 |
| stop (Stop) | 이 세션이 고친 코드, 하네스, 상태 파일에 커밋하지 않은 변경이 있을 때. 이번 턴에 고친 문서는 알리기만 한다. 이번 턴에 서버 작업을 띄웠거나 ssh로 저장소 코드를 run_in_background로 돌렸는데 답에 남은 시간과 끝나는 시각이 없을 때(PreToolUse hook이 막아 돌지 않은 명령은 세지 않는다). 이번 턴에 다룬 계획이 stop_states에 머물 때. 일반 질문이나 부가 작업 턴은 앞 턴의 계획 상태로 막지 않는다. 5번 연속 막으면 통과시킨다 |
| pre-commit | 커밋할 코드 파일이 코드 상태인 계획의 files_section 절에 없을 때. 그 파일을 적은 계획이 edit = "tests" 상태인데 테스트 파일이 아닐 때. 그 파일을 적은 계획이 종류의 limit을 벗어났을 때 |
| commit-msg | 하네스 파일과 다른 파일이 한 커밋에 섞일 때. 하네스 커밋의 메시지가 `harness:`로 시작하지 않을 때 |

읽기 명령은 cd, cat, head, tail, ls, wc, grep, rg, cut, echo, du, stat, nl, Get-Content, Select-String과 git add, commit, diff, log, show, status, checkout, restore, blame, grep, ls-files, rev-parse, branch이고, 따옴표 밖의 &&, ||, ;, |로 이을 수 있으며 `2>/dev/null`, `2>&1`만 붙일 수 있다. 다른 리다이렉트, 명령 치환, 배경 실행이 있으면 읽기로 보지 않고, awk, sed, find, xargs는 파일을 쓰거나 명령을 돌릴 수 있어 읽기에서 뺀다.

SessionStart는 진행 중인 계획과 할 일, status_file 절의 항목 머리를 6,000자 안으로 넣는다. hook 출력이 10,000자를 넘으면 Claude Code가 앞 2KB만 넣기 때문이다.

guard와 stop이 막은 기록(guard는 명령 앞 200자 포함), `next`가 통과하지 못한 이유, 막혔던 행동이 통과한 기록은 git 폴더의 gate-events.jsonl에 남아 `diagnose`가 쓴다. 마지막 구조 변경 뒤 끝난 계획이나 guard와 stop이 막은 횟수가 기준을 넘거나 프로젝트 단계가 바뀌면 SessionStart가 하네스 점검을 권한다.
