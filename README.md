<!-- 문체 점검: GitHub에 공개하는 README -->

# dynamic-stagegate-harness

Claude Code 에이전트의 작업을 단계로 나누고 단계마다 넘어가는 조건을 검사하며, 진행 기록을 근거로 그 단계 구조를 고쳐 가는 하네스 플러그인이다.

![version](https://img.shields.io/badge/version-0.3.0-555)
![license](https://img.shields.io/badge/license-MIT-555)
![python](https://img.shields.io/badge/python-3.11%2B-555)
![Claude Code plugin](https://img.shields.io/badge/Claude%20Code-plugin-555)

각 단계는 state machine의 상태이고, 작업마다 계획 파일 하나와 상태 경로가 있다. 상태마다 할 일과 넘어가는 조건이 있고, 조건은 모델의 판단이 아니라 계획 파일, git 기록, 테스트나 eval 명령, 결과 파일로 검사한다. 상태에 맞지 않는 행동은 Claude Code hook과 git hook이 막는다.

상태 경로는 프로젝트가 자라면서 구조 변경 제안서로 바꿀 수 있다. 제안서는 에이전트가 진행 기록을 진단해 쓰고, lint와 지난 계획 재생과 검토자를 거쳐 사용자가 승인해야 적용된다. 이름은 단계마다 관문을 통과해야 다음으로 넘어가는 Stage-Gate 방식에서 따왔고, 관문 구조가 바뀐다는 뜻으로 dynamic을 붙였다.

## 동작 방식

서비스 코드 프로젝트의 feature 작업은 아래 경로를 지난다. 검토 표시가 있는 상태는 읽기 도구만 쓰는 별도 Claude 호출인 검토자가 PASS를 줘야 넘어간다. 코드 표시가 있는 상태에서만 코드 편집과 커밋이 열리고, 테스트 코드 표시가 있는 상태에서는 테스트 파일만 열린다. 넘어가는 조건을 채우지 못하면 그 상태에 머문다. 앞 상태에서 정한 내용이 틀렸으면 `back` 명령으로 되돌아가는데, 종류마다 적어 둔 경로로만 갈 수 있다. 다이어그램의 점선이 그 경로이고, 점선 상자로 묶은 TEST와 REVIEW는 둘 다 IMPLEMENT, PLAN, SPEC으로 돌아갈 수 있다.

```mermaid
flowchart LR
    start(( ))
    done((( )))
    SPEC["SPEC<br>검토"]
    PLAN["PLAN<br>검토"]
    TESTFIRST["TESTFIRST<br>테스트 코드"]
    IMPLEMENT["IMPLEMENT<br>코드"]
    subgraph back0 [" "]
        direction LR
        TEST
        REVIEW["REVIEW<br>검토"]
    end
    style back0 fill:none,stroke:#999,stroke-dasharray:4 3
    RECORD
    start --> SPEC
    SPEC --> PLAN
    PLAN --> TESTFIRST
    TESTFIRST --> IMPLEMENT
    IMPLEMENT --> TEST
    TEST --> REVIEW
    REVIEW --> RECORD
    RECORD --> done
    back0 -.-> IMPLEMENT
    back0 -.-> PLAN
    back0 -.-> SPEC
```

TESTFIRST에서는 테스트 파일만 고칠 수 있고 구현 전에 쓴 테스트가 실패해야 넘어간다. TEST는 그 테스트와 프로젝트 테스트 명령이 통과하고 수용 기준마다 "테스트 결과" 표에 행이 있어야 넘어간다. TEST는 SPEC에서 검토받은 절과 TESTFIRST에서 실패했던 새 테스트 명령이 그 뒤에 바뀌지 않았는지 본다. TESTFIRST에서 쓴 테스트 파일을 그 뒤에 고쳤으면 "테스트 수정" 표에 이유가 적혀 있어야 TEST가 넘어간다. REVIEW의 검토자는 계획을 시작한 뒤의 코드 diff를 받아 계획 파일과 견주고, 고친 테스트가 약해졌는지도 본다. 에이전트가 순서를 건너뛰면 다음처럼 막히고, 막힌 이유와 다음에 할 일을 같이 받는다.

```
# 계획 없이 코드를 고치려 할 때 (PreToolUse hook)
코드를 고치는 상태인 계획이 없어 src/app.py 수정을 막았다. python -X utf8 .gate/gate.py status로
상태를 보고, 계획을 시작하거나 next로 코드 상태까지 진행한다.

# 계획 파일을 채우지 않고 다음 상태로 넘기려 할 때
$ python -X utf8 .gate/gate.py next P001
P001 SPEC에서 넘어가지 못했다. 빠진 것:
  - '요구사항' 절이 비었다
  - '수용 기준' 절 표에 칸이 모두 찬 행이 1개 이상 있어야 한다(지금 0개)
  - '범위 밖' 절이 비었다
```

## 빠른 시작

Python 3.11 이상과 git이 필요하다.

1. 마켓플레이스를 추가한다. 저장소를 받아 둔 경우에는 GitHub 경로 대신 로컬 폴더 경로를 줘도 된다.
   ```
   claude plugin marketplace add taejeong0416/dynamic-stagegate-harness
   ```
2. 플러그인을 설치한다.
   ```
   claude plugin install dynamic-stagegate-harness@dynamic-stagegate-harness
   ```
3. 하네스를 쓸 프로젝트에서 Claude Code를 열고 설치 skill을 부른다.
   ```
   /dynamic-stagegate-harness:setup
   ```
4. 고정 질문 다섯 개(하는 일, 실행 위치, 산출물, 테스트 명령, 실패 비용)에 답한다. 답과 저장소의 사실에 조건이 맞는 부품이 조립되어 첫 구조가 된다.
5. 이어지는 목적 인터뷰에 답한다. 답은 `.gate/PROFILE.md`에 남고 첫 구조 조정에 쓰인다.

플러그인을 사용자 범위로 설치해도 `.gate/config.toml`이 없는 프로젝트에서는 hook이 아무것도 하지 않는다.

## 주요 기능

- 설치할 때 고정 질문의 답과 저장소의 사실로 부품을 골라 구조를 조립하고, 목적 인터뷰로 프로젝트의 목적, 검증 방법, 작업 종류를 정해 그 구조를 조정한다.
- 구조는 부품 단위로 관리한다. 부품마다 버전과 파일 해시가 `.gate/parts.lock`에 남아서, 라이브러리에서 고쳐진 부품과 프로젝트에서 고친 부품을 `gate parts`로 구분해 본다.
- `gate next`가 넘어가는 조건을 검사하고 조건이 맞는 다음 상태로 계획을 옮긴다. 경로에는 조건을 붙일 수 있어서, 마이그레이션이 바뀐 계획만 MIGRATE에 들르게 하거나 `--with quick`으로 시작한 계획은 SPEC을 건너뛰게 할 수 있다.
- 종류의 `back`에 되돌아가는 경로를 적어 두면 그 경로로만 돌아가고, 경로는 다이어그램에 점선으로, 진단에 경로별 횟수로 나온다.
- `unchanged` 검사는 상태를 넘길 때 절 내용을 기록해 두고 뒤 상태에서 그 내용이 바뀌었으면 막는다. 실패했던 테스트 명령이나 검토받은 수용 기준을 나중에 고쳐서 통과하지 못한다. `gate run`을 적었거나 실행 결과가 생긴 뒤에는 그 기록을 고정해서, back으로 돌아가 다시 넘겨도 결과를 본 뒤 판정 경계나 eval 케이스를 바꿀 수 없다.
- `code_unchanged` 검사는 실행 상태에 들어간 뒤 계획의 코드가 바뀌었으면 막는다. 실험 경로의 RUN과 에이전트 경로의 EVAL에 붙어 있어서, 결과가 앞에서 검토받은 코드에서 나왔는지 확인한다.
- `test_edits` 검사는 먼저 쓴 테스트, 재현 테스트, 고정한 eval 케이스, refactor 전의 테스트를 고치거나 지운 계획을 막는다. 고친 파일과 이유를 "테스트 수정" 표에 쓰면 넘어가고, REVIEW 검토자가 그 변경을 다시 본다.
- `covers` 검사는 한 표의 첫 칸 값이 모두 다른 표의 첫 칸에 있는지 본다. feature는 수용 기준마다 "테스트 결과" 표에 행이 있어야 TEST를 넘긴다.
- 실패해야 하는 명령은 테스트를 돌리지 못한 경우를 실패로 치지 않는다. 명령의 프로그램을 찾지 못하거나 종료 코드가 4, 5, 126, 127이면 막는다. 같은 명령과 같은 테스트 파일로 실패를 확인한 기록이 있으면 다시 돌리지 않아서, 구현한 뒤 back으로 TESTFIRST나 REPRO에 돌아와도 넘어갈 수 있다.
- 검토자는 상태마다 모델을 고를 수 있다. 라이브러리 부품은 SPEC과 PLAN 같은 계획 문서 검토를 sonnet으로, 코드 diff를 보는 REVIEW를 기본 모델로 돌린다. 검토자 호출은 사용자 설정을 읽지 않아서 추론 단계는 `[review] effort`로 정하고, 라이브러리의 기본값은 high다. MCP 서버와 skill 목록은 검토자에게 넣지 않는다.
- 기능 경로, 결함 경로, 에이전트 경로의 구현 상태인 IMPLEMENT와 FIX는 할 일에서 코드 작성을 sonnet 서브에이전트에 맡기라고 안내한다. 먼저 쓴 테스트나 재현 명령이 구현을 검사하므로, 테스트와 계획은 메인 세션의 모델이 쓰고 구현만 sonnet에 맡긴다.
- 검토자 지시문에 `{diff}`를 넣으면 계획을 시작한 뒤의 코드 diff를 파일로 받는다. 같은 계획 내용으로 통과한 뒤에는 그 뒤에 바뀐 코드만 다시 검토하고, 바뀐 코드가 없으면 부르지 않는다. diff 머리에는 "변경 파일" 표에 있는데 바뀌지 않은 파일과 표에 없는데 바뀐 파일이 적힌다. 실험 경로는 IMPLEMENT에서 코드 diff를 검토받아, 학습과 채점의 전처리 차이, 대조군과 다른 표본 추출, 결측 처리, 누수를 실행 전에 본다.
- 지시문에 `{context}`를 넣으면 상태의 context에 적은 문서에서 뽑은 발췌를 받는다. 큰 문서는 제목과 제목마다 첫 줄, 계획이 인용한 참조가 든 줄만 담아서, 계속 커지는 결정 기록이나 실험 기록을 호출마다 통째로 읽지 않는다.
- 종류에 `limit` 조건을 두면 그 조건을 벗어난 계획은 커밋과 `next`가 막힌다. chore는 계획에 적은 코드 파일이 5개를 넘으면 다른 종류로 다시 시작해야 한다.
- 기능 경로와 함께 쓰는 chore는 `[project] affected` 명령이 통과해야 끝난다. affected에는 `npx vitest related --run {files}`처럼 계획의 코드 파일과 관련된 테스트만 돌리는 명령을 넣고, 비어 있으면 전체 테스트 명령을 쓴다.
- 코드 편집은 코드 상태인 계획의 "변경 파일" 표에 적은 파일에만 열리고, TESTFIRST, REPRO, EVALSET처럼 테스트만 쓰는 상태에서는 테스트가 아닌 코드 파일의 편집과 커밋을 막는다. 계획의 "변경 파일" 표에 없는 코드 파일은 커밋을 막고, 구조 파일인 config.toml, history, gatekit과 git hook을 직접 편집하는 것도 막는다.
- 이 세션이 고친 코드나 하네스 파일에 커밋하지 않은 변경이 있거나, 이번 턴에 다룬 계획이 멈추면 안 되는 상태에 있으면 턴을 끝내지 못하게 한다. 일반 질문이나 부가 작업 턴, 다른 세션의 작업으로는 막지 않는다.
- SessionStart hook이 진행 중인 계획과 할 일, 상태 문서 절의 항목 머리를 6,000자 안으로 넣고, 메시지마다 계획 목록이 한 줄씩 들어간다. hook 출력이 10,000자를 넘으면 Claude Code가 앞 2KB만 넣기 때문이다. 컨텍스트가 압축돼도 상태는 파일에 남아 있어서 다시 들어온다.
- `gate describe --mermaid`가 지금 구조의 경로를 위와 같은 다이어그램으로 출력한다.

## 부품

구조는 부품을 조립해 만든다. 부품은 하나의 기능을 이루는 상태, 절, 검사, 설정의 묶음이고, 부품마다 적용 조건이 있다. 설치할 때 고정 질문의 답과 저장소의 사실로 조건을 판정해서, 조건이 맞은 부품만 `.gate/parts/`에 복사하고 `.gate/config.toml`로 조립한다.

| 부품 | 내용 | 적용 조건 |
|---|---|---|
| experiment | 실험 경로. new와 model(RESEARCH부터 REPLAN까지 8개), tune, freeze, repro | 하는 일이 실험 |
| measure | 측정 경로 eda(QUESTION, MEASURE, RECORD, REPLAN) | 하는 일이 실험 |
| infra | 공용 코드를 고치는 작은 경로 infra | 하는 일이 실험 |
| competition-stages | 대회 단계 다섯 개와 단계별 허용 종류 | 하는 일이 실험이고 산출물을 밖으로 낸다 |
| run-record | 실행 상태에서 `gate run` 기록과 코드 고정을 요구하고 학습 서버 설정을 둔다 | 오래 걸리는 실행을 원격에서 돌린다 |
| value-evidence | 코드에 넣은 숫자 값마다 근거를 적게 한다 | 하는 일이 실험 |
| sample-run | 긴 실행 전에 작은 표본으로 끝까지 돌려 보게 한다 | 실패하면 시간을 잃는다 |
| experiment-diff-review | 실험 코드 diff를 계획과 견주는 검토 | 하는 일이 실험 |
| feature | 기능 경로. feature(SPEC, PLAN, IMPLEMENT, TEST, RECORD)와 refactor | 하는 일이 서비스 코드 |
| test-first | 구현 앞에 TESTFIRST를 넣어 실패하는 테스트를 먼저 쓰고 고정한다 | 하는 일이 서비스 코드이고 테스트 명령이 있다 |
| diff-review | 확인 상태 뒤에 코드 diff를 검토하는 REVIEW를 넣는다 | 하는 일이 서비스 코드 |
| bugfix | 결함 경로 bugfix(REPRO, FIX, VERIFY, RECORD). 재현 명령이 REPRO에서 실패하고 FIX에서 통과해야 한다 | 하는 일이 서비스 코드나 에이전트 |
| chore | 작은 변경 경로 chore(IMPLEMENT, RECORD). 파일 수 상한이 있다 | 하는 일이 서비스 코드나 에이전트 |
| eval | 에이전트 경로. behavior(SPEC, EVALSET, IMPLEMENT, EVAL, RECORD, REPLAN)와 prompt. eval 케이스를 구현 전에 커밋하고 고정한다 | 하는 일이 에이전트 |

경로 부품은 상태에 역할(plan, implement, execute, verify, record, decide)을 붙이고, 다른 부품은 그 역할에 붙는다. diff-review는 확인 역할 뒤에 REVIEW를 넣으므로 기능 경로의 TEST 뒤와 결함 경로의 VERIFY 뒤에 같은 상태가 들어가고, 에이전트 경로와 조립하면 EVAL 뒤에 들어간다. run-record는 실행 역할인 상태에 붙는다.

설치하면 라이브러리 부품 전체가 `.gate/library/`에 같이 들어간다. 폴더 패턴이나 서버 주소처럼 프로젝트마다 다른 값은 `.gate/local.toml`에 두면 조립 결과를 덮어쓴다. `gate parts`는 쓰는 부품 가운데 조건이 거짓이 된 것과 쓰지 않는 부품 가운데 조건이 맞게 된 것을 알린다.

상태와 절의 이름은 구조 전체에서 하나라서, 같은 이름을 다르게 정의하는 부품은 함께 조립되지 않는다. 지금 라이브러리에서는 실험 경로, 기능 경로, 에이전트 경로가 PLAN, IMPLEMENT, RECORD를 서로 다르게 정의해서 한 프로젝트에 둘 이상 넣을 수 없다. 설치 스크립트는 이런 조합을 받으면 충돌하는 이름을 출력하고 멈춘다.

## 구조 진화

구조는 제안서로만 바꾼다. SessionStart hook은 마지막 구조 변경 뒤 끝난 계획 수나 hook이 막은 횟수가 기준을 넘으면 점검을 권한다.

1. `gate diagnose`가 상태별로 들어온 횟수, back, 건너뜀, 머문 시간, 절 글자 수, 검토 FAIL 이유, 검토 호출 수와 비용, 되돌아간 경로별 횟수를 모으고, hook이 막은 기록, `next`가 통과하지 못한 이유별 횟수, 저장소 변화도 모은다.
2. 에이전트가 `gate propose H###`로 지금 부품의 사본을 만들어 부품을 더하고 빼고 고친 뒤, 그 수치를 근거로 `.gate/proposals/H###.md`에 제안서를 쓴다. 상태나 검사를 더하는 변경은 근거 하나로 충분하고, 빼거나 약하게 하는 변경은 진단 수치가 있어야 한다.
3. `gate check-proposal`이 사본을 조립해서 부품별 변경, lint, 새 구조에서 빠지거나 풀리는 검사와 상태와 설정([project], [review], [remote], [[stages]])의 목록, 지난 계획 재생, diff, 새 경로 다이어그램을 진단 파일에 쓰고 검토자를 부른다. 검토자는 그 목록 가운데 "바꾸는 것" 표에 근거가 없는 변경을 찾는다.
4. 사용자가 바꾸는 것과 근거를 보고 승인하면 `gate adopt`가 부품, local.toml, config.toml, parts.lock을 함께 바꾼다. guard hook은 승인 문장이 이번 턴 사용자 입력에 있는지 확인한다.

앞 구조는 `.gate/history/`에 남고, 진행 중인 계획은 시작할 때의 구조 버전을 끝까지 쓴다. 종류와 상태뿐 아니라 code 패턴, 변경 파일 절 이름, max_visits, stop_states, 검토 모델과 추론 단계도 그 버전을 따르고, 엔진이 바뀐 진행 중 계획은 status와 SessionStart가 알린다. 아래는 마이그레이션 단계와 작은 작업용 선택을 더한 뒤의 feature 경로 예시다.

```mermaid
flowchart LR
    start(( ))
    done((( )))
    SPEC["SPEC<br>검토"]
    PLAN["PLAN<br>검토"]
    TESTFIRST["TESTFIRST<br>테스트 코드"]
    IMPLEMENT["IMPLEMENT<br>코드"]
    MIGRATE["MIGRATE<br>코드"]
    subgraph back0 [" "]
        direction LR
        TEST
        REVIEW["REVIEW<br>검토"]
    end
    style back0 fill:none,stroke:#999,stroke-dasharray:4 3
    RECORD
    start -->|"--with quick 아님"| SPEC
    start -->|"SPEC 건너뜀"| PLAN
    SPEC --> PLAN
    PLAN --> TESTFIRST
    TESTFIRST --> IMPLEMENT
    IMPLEMENT -->|"migrations/* 변경"| MIGRATE
    IMPLEMENT -->|"MIGRATE 건너뜀"| TEST
    MIGRATE --> TEST
    TEST --> REVIEW
    REVIEW --> RECORD
    RECORD --> done
    back0 -.-> IMPLEMENT
    back0 -.-> PLAN
    back0 -.-> SPEC
```

## 넘어가는 조건

조립한 구조는 프로젝트의 `.gate/config.toml`에 있고, 키 설명과 명령은 GATE.md에 있다. 상태마다 아래 검사를 골라 붙이며, 검사마다 `only = [종류]`나 `skip = [종류]`로 적용할 종류를 고른다.

| 검사 | 통과 조건 |
|---|---|
| filled | 절이 비어 있지 않다. items를 주면 그 bullet 항목마다 값이 있다 |
| table | 절의 표에 칸이 모두 찬 행이 min_rows개 이상 있다 |
| files_listed | "변경 파일" 절에 code 패턴에 맞는 파일이 있다 |
| files_changed | 이 상태에 처음 들어온 뒤의 커밋에서 그 파일이 바뀌었다(any = true면 하나 이상) |
| clean | 코드에 커밋하지 않은 변경이 없다 |
| numbers | 코드에 새로 넣은 숫자(0과 1 제외)가 근거 표 첫 칸에 있다 |
| sample | 표본 실행 절의 커밋이 코드 파일을 마지막으로 바꾼 커밋이다 |
| results | 이 상태에 들어온 뒤에 결과 파일이 만들어졌다([remote]가 있으면 서버도 본다) |
| command | 명령이 통과한다. use = "test"나 "affected"면 [project]의 그 명령을 쓰고, expect = "fail"이면 테스트가 돌고 실패해야 한다 |
| run_record | `gate run` 기록이 있다 |
| code_unchanged | 이 상태에 들어온 뒤 계획의 코드 파일에 커밋이나 커밋하지 않은 변경이 없다 |
| commit_since | 이 상태에 들어온 뒤 path를 바꾼 커밋이 있다(mention = true면 계획 번호를 쓴 줄) |
| choice | 고른 갈래가 결과별 다음 행동 표의 첫 칸 하나이거나 escape 문구다 |
| refs | 절에서 pattern에 맞는 참조가 file에 모두 있다 |
| paths_exist | 절에 적은 파일 경로가 모두 있다 |
| reviewed | state에서 받은 검토 뒤 계획 내용이 바뀌지 않았다 |
| unchanged | state를 넘긴 뒤 그 상태가 쓰는 절이 바뀌지 않았다(sections로 절을, section과 items로 bullet 항목을 고른다) |
| test_edits | state를 처음 넘긴 뒤 고치거나 지운 테스트 파일이 section 표에 있다. state가 없으면 계획을 시작한 뒤를 본다 |
| covers | from 절 표의 첫 칸이 모두 section 절 표의 첫 칸에 있다 |

## 비슷한 접근과 차이

LLM 작업을 state machine이나 그래프로 나누는 방법은 이미 여럿 있는데, 이 하네스는 에이전트 런타임을 새로 만들지 않은 채 Claude Code 바깥에서 관문을 검사한다.

| 접근 | 하는 일 | 이 하네스와 다른 점 |
|---|---|---|
| StateFlow(2024 논문) | LLM 작업을 상태와 전이로 나누고, 상태마다 프롬프트와 도구를 정한다 | 같은 분해를 쓰되, 전이 조건을 모델이 아니라 파일, git, 명령 결과로 검사한다 |
| LangGraph | 노드와 간선으로 에이전트를 구성하고 상태를 체크포인트로 남긴다 | 자체 런타임이 아니라 Claude Code hook과 git hook 위에서 돈다. 경로는 조건부로 건너뛰는 선형 경로와 적어 둔 되돌아가는 경로로 제한해 lint와 재생이 가능하게 했다 |
| TDD Guard, Probity | hook으로 에이전트의 파일 쓰기와 명령을 규칙에 따라 막는다 | 테스트 상태 하나가 아니라 명세, 계획, 구현, 기록으로 이어지는 작업 단계 전체를 보고 막을 행동을 정한다. 같은 편집이 IMPLEMENT에서는 열리고 SPEC에서는 막힌다 |
| Superpowers, Spec Kit | skill과 템플릿으로 설계, 계획, 구현, 검토 절차를 안내한다 | 절차를 따르는지를 모델에 맡기지 않고, 상태를 넘길 때마다 검사한다 |
| Anthropic의 장시간 에이전트 하네스 | 기능 목록과 진행 파일로 여러 세션의 작업을 잇는다 | 상태 파일을 gate 명령만 쓰게 해서 세션이 바뀌어도 진행 상태가 깨지지 않는 점은 같다. 여기에 구조 자체를 바꾸는 절차를 더했다 |

## 폴더 구조

```
.claude-plugin/marketplace.json      로컬 마켓플레이스
plugins/dynamic-stagegate-harness/
  .claude-plugin/plugin.json
  hooks/hooks.json, run.py            설정이 있는 프로젝트에서만 그 프로젝트의 엔진을 부른다
  gatekit/                            엔진(설치하면 프로젝트의 .gate/gatekit/로 복사된다)
    core.py      설정과 구조 버전, 조건부 경로, 상태 파일, 계획 파일 파싱, git
    checks.py    넘어가는 조건 검사 함수와 검토자 호출
    evolve.py    lint, describe, diagnose, 제안서 검사, adopt
    parts.py     부품 조립, 적용 조건, parts.lock
    cli.py       gate 명령과 git hook 검사
    hooks.py     guard, stop, 주입 hook
  library/<부품>/part.toml            부품(종류, 상태, 절, 붙일 자리, 적용 조건)
  GATE.md                             프로젝트에 복사되는 명령과 원칙 설명
  scripts/install.py                  설치와 엔진 갱신
  skills/setup, gate, evolve/         설치와 목적 인터뷰, 운영, 구조 다시 짜기
tests/test_gate.py                    임시 저장소에 설치해 명령과 hook을 돌려 보는 테스트
tests/combos/                         실제로 돌아 본 조합의 기대 구조(답으로 조립한 결과와 견준다)
```

테스트는 저장소 루트에서 `python -X utf8 -m unittest discover tests`로 돌린다.

설치한 프로젝트의 `.gate/`에는 parts/(쓰는 부품), local.toml(고정 질문의 답과 덮어쓰기), parts.lock(부품 버전과 해시), config.toml(조립한 지금 구조), history/(앞 구조 버전), library/(부품 라이브러리), proposals/(제안서와 진단), PROFILE.md(목적), state.json(계획 상태), GATE.md가 들어간다.

엔진을 프로젝트에 복사하면 엔진 버전이 프로젝트 git에 같이 남아서 나중에 다시 실행해도 같은 규칙이 적용된다. 플러그인을 갱신한 뒤에는 `/dynamic-stagegate-harness:setup update`로 엔진만 바꾸고 `harness:` 커밋으로 남긴다. 엔진의 앞 버전은 보관하지 않아서, 진행 중인 계획이 있으면 update가 경고한다.

## 한계

- Claude Code 전용이다. 엔진과 git hook은 에이전트와 관계없이 돌지만, 편집 차단과 턴 종료 검사는 Claude Code의 hook 형식과 대화 기록 형식에 맞춰져 있다.
- 셸 명령 검사는 리다이렉트, tee, sed -i, cp, mv 같은 흔한 우회만 막고(읽기 명령은 따옴표 밖의 &&, ;, |로 이을 수 있다), 그 밖의 방법으로 쓴 코드는 커밋 시점의 pre-commit이 잡는다. 구조 파일을 Python 스크립트로 직접 쓰면 guard가 보지 못하고, commit-msg hook이 `harness:` 커밋으로 따로 남기게 할 뿐이다.
- stop hook은 이 세션이 편집 도구로 고친 파일만 본다. 셸 명령이나 스크립트로 고친 파일은 pre-commit과 `clean` 검사가 잡는다.
- 숫자 검사는 .py만 tokenize로 정확히 보고, 다른 언어는 문자열과 주석을 지운 뒤 정규식으로 본다.
- 진단 수치는 판단 자료이고, 상태를 빼거나 더할지는 에이전트의 제안과 사용자 승인으로 정한다.

## 라이선스

MIT 라이선스로 배포한다. 전문은 LICENSE 파일에 있다.

## 참고 자료

- StateFlow: Enhancing LLM Task-Solving through State-Driven Workflows (Wu 외, 2024) https://arxiv.org/abs/2403.11322
- LangGraph https://github.com/langchain-ai/langgraph
- TDD Guard https://github.com/nizos/tdd-guard
- Probity https://github.com/nizos/probity
- Superpowers https://github.com/obra/superpowers
- Spec Kit https://github.com/github/spec-kit
- Effective harnesses for long-running agents (Anthropic) https://www.anthropic.com/engineering/effective-harnesses-for-long-running-agents
- Claude Code hooks https://code.claude.com/docs/en/hooks
