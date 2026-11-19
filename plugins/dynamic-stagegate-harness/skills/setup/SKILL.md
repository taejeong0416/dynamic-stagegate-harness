---
name: setup
description: 프로젝트에 stagegate state machine 하네스를 설치한다. 고정 질문의 답과 저장소의 사실로 부품을 골라 구조를 조립하고, 목적을 묻는 인터뷰로 PROFILE.md와 첫 조정을 정한다. 엔진 갱신(update)도 하고, statusline에 계획 상태를 띄울지도 묻는다. 사용자가 "stagegate 하네스 설치", "setup", "하네스 깔아줘", "하네스 갱신"이라고 할 때 쓴다.
argument-hint: "[update]"
---

# stagegate 하네스 설치

인자: `$ARGUMENTS` (없으면 새로 설치, update면 엔진 갱신)

구조는 부품을 조립해 만든다. 부품마다 적용 조건이 있어서, 고정 질문의 답과 저장소의 사실에 조건이 맞는 부품만 들어간다. 부품 목록과 조건은 `${CLAUDE_PLUGIN_ROOT}/library/<이름>/part.toml`에 있다.

## 1. 고정 질문

1. 프로젝트 루트가 git 저장소이고 커밋이 하나 이상 있는지 본다. 없으면 사용자에게 `git init`과 첫 커밋을 먼저 하자고 한다.
2. 프로젝트에 이미 다른 하네스(`.claude/settings.json`의 state machine hook, `.githooks/`)가 있으면 멈추고 사용자에게 알린다. 두 하네스를 함께 돌리지 않는다.
3. 아래 질문을 한 번에 하나씩 묻는다. 저장소를 읽어 알 수 있는 사실(테스트 설정 파일, 서버 설정, 제출 폴더)은 찾아서 권장 답에 넣는다.

| 질문 | 키 | 답 |
|---|---|---|
| 이 저장소에서 하는 일 | work | experiment(지표로 비교하는 실험), service(서비스나 라이브러리 코드), agent(에이전트, 프롬프트) |
| 오래 걸리는 실행을 어디서 돌리는가 | run | local, remote |
| 산출물을 밖으로 내보내는가(제출, 배포, 릴리스) | ships | yes, no |
| 테스트 명령이 있는가 | tests | yes, no |
| 실패하면 무엇을 잃는가 | failure_cost | reversible(되돌릴 수 있음), time(시간 손실), irreversible(되돌릴 수 없음) |

## 2. 설치

1. 프로젝트 밖 임시 폴더에 입력 파일을 쓴다. `[answers]`에 답을 넣고, 이 프로젝트의 폴더에 맞춘 `[project] code`(고칠 때 코드 상태인 계획이 있어야 하는 파일)와 `tracked`(stop hook이 미커밋 변경을 보는 폴더)를 넣는다. 테스트 명령과 테스트 파일 패턴을 알면 `test`, `affected`, `tests`도 넣는다.
   ```toml
   [answers]
   work = "service"
   run = "local"
   ships = "yes"
   tests = "yes"
   failure_cost = "reversible"

   [project]
   code = ["src/*", "tests/*"]
   tracked = ["src", "tests", "plans", "docs", ".gate", ".githooks"]
   test = "npm test"
   ```
2. 고른 부품을 먼저 본다. 조건이 맞은 부품과 뺀 부품이 조건과 함께 나온다.
   ```
   python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" --local "<입력 파일>" --dry-run --target "<프로젝트 루트>"
   ```
3. 목록을 사용자에게 보이고 승인을 받는다. "함께 조립되지 않는다"가 나오면 충돌하는 이름을 그대로 알린다. 실험, 서비스, 에이전트 경로는 지금 한 프로젝트에 하나만 넣을 수 있으므로 주로 하는 일 하나를 고르게 한다.
4. 승인받으면 `--dry-run`을 빼고 다시 돌린다.
5. 하네스 파일만 먼저 커밋한다. `git add .gate .githooks`, `git reset .gate/state.json`, `git commit -m "harness: stagegate 하네스 설치"`. 상태 파일은 따로 커밋한다.
6. `python -X utf8 .gate/gate.py describe`로 조립된 구조를 보여 준다.

## 3. 목적 인터뷰

grilling 방식으로 묻는다. 질문은 한 번에 하나씩 하고, 질문마다 권장 답과 이유를 붙이고, 답을 듣고 다음으로 넘어간다. 저장소를 읽어 알 수 있는 사실은 묻지 않고 찾아서 권장 답에 넣는다. 결정은 사용자에게 묻는다.

순서대로 묻되, 고정 질문이나 앞 답으로 정해진 질문은 건너뛴다.

| 주제 | 묻는 것 | 바뀌는 구조 |
|---|---|---|
| 목적 | 무엇을 만들고, 언제 성공했다고 보는가(점수, 출시, 학습) | PROFILE의 기준, REPLAN 같은 결과 판단 상태의 유무 |
| 검증 | 무엇으로 맞았는지 확인하는가(단위 테스트, e2e, eval, 로컬 점수, 사람 확인)와 그 명령. 저장소의 테스트 도구로 관련 테스트만 돌리는 명령(vitest related, jest --findRelatedTests, pytest-testmon)과 테스트 파일 위치를 찾아 권장 답에 넣는다 | local.toml의 [project] test, affected, tests, command 검사 |
| 작업 종류 | 주로 하는 일과 그 비율(기능, 버그, 실험, 프롬프트 조정, 잡일) | 쓰지 않을 경로 부품 빼기 |
| 산출물 | 코드 말고 바뀌는 것(DB 마이그레이션, 인프라 설정, 문서, 제출 파일) | 새 상태(MIGRATE 같은)를 넣는 project 부품과 when 조건, code 패턴 |
| 단계 | 프로젝트가 단계별로 다르게 일하는가(탐색, 개발, 동결) | [[stages]]와 단계별 허용 종류 |

인터뷰가 끝나면 정리한 답을 보여 주고 맞는지 확인받는다. 확인받기 전에는 파일을 쓰지 않는다.

## 4. PROFILE.md와 첫 조정

1. `.gate/PROFILE.md`에 답을 쓴다. 절은 목적, 성공 기준, 검증 방법, 주로 하는 작업, 실패 비용, 산출물, 코드 위치, 실행 환경, 단계다. 사용자의 말을 그대로 옮기고 해석을 덧붙이지 않는다.
2. 조립된 구조와 PROFILE이 맞지 않는 곳을 찾는다. 바꿀 곳이 없으면 PROFILE만 커밋하고 끝낸다.
3. 바꿀 곳이 있으면 evolve skill의 제안서 절차로 H001을 쓴다. 근거 칸에는 PROFILE의 해당 절을 인용한다. `propose H001`, `check-proposal H001 --review`, `adopt` 순서로 적용한다(approval이 user이면 adopt 전에 사용자 승인을 받는다).

## 5. statusline

statusline은 프로젝트가 아니라 사용자 설정 `~/.claude/settings.json`에 켜는 선택 기능이고, CLAUDE_CONFIG_DIR가 있으면 그 폴더의 설정에 켠다. 켜면 하네스가 있는 프로젝트에서 진행 중인 계획마다 번호, 종류, 상태 경로, 예상 종료 시각이 한 줄씩 statusline에 나온다.

1. 켜져 있는지 본다.
   ```
   python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/statusline.py" --check
   ```
2. "켜짐"이면 묻지 않고 넘어가고, "꺼짐"이면 사용자에게 statusline에 계획 상태를 띄울지 묻는다. 지금 쓰는 statusLine이 출력에 나오면 그 명령은 지우지 않고 그 명령의 출력 아래에 계획 줄을 붙인다고 함께 알린다.
3. 원한다고 답하면 켠다. 출력을 그대로 보여 주고, 새 세션부터 보인다고 알린다.
   ```
   python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/statusline.py" --install
   ```
4. 끄려면 `--uninstall`을 쓴다. statusLine이 켜기 전 명령으로 돌아간다.

## 갱신(update)

플러그인을 새 버전으로 받은 뒤 프로젝트의 엔진과 라이브러리 복사본을 바꾼다. 쓰는 부품(`.gate/parts`), local.toml, config.toml, PROFILE.md는 건드리지 않는다.

```
python -X utf8 "${CLAUDE_PLUGIN_ROOT}/scripts/install.py" --update --target "<프로젝트 루트>"
```

출력된 명령으로 하네스 커밋을 남기고, `python -X utf8 .gate/gate.py lint`가 통과하는지 본다. `python -X utf8 .gate/gate.py parts`에 "라이브러리에 v2가 있다"처럼 새 버전이 나온 부품은 사용자에게 알리고, 받으려면 evolve skill의 제안서로 그 부품 폴더를 라이브러리 것으로 바꾼다. 진행 중인 계획이 있으면 update가 경고를 출력한다. 그 계획은 구조 버전은 그대로 쓰지만 바뀐 엔진의 검사를 받으므로 사용자에게 알린다. `statusline.py --check`로 statusline이 켜져 있는지 보고, 켜져 있으면 `--install`을 다시 돌려 설정 폴더의 사본을 새 버전으로 바꾼다.
