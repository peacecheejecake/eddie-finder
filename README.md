# eddie-finder

키이스케이프 예약 오픈 순간을 잡아 STEP 2(정보 입력)까지 자동으로 진행하는 CLI.
reCAPTCHA 체크와 최종 **[예약하기]** 클릭은 직접 한다.

## 동작

1. 지점/테마 이름을 사이트 ID로 변환 (오타 허용: `FLIM BY EDDIE` → `FILM BY EDDY`)
2. 오픈 시각 = 이용일 6일 전 + 지점별 오픈시간(KST). 서버 Date 헤더로 시계 오차 보정
3. Chrome 창을 띄워 예약 페이지를 미리 열어둠 (오픈 20초 전 새로고침)
4. 오픈 `--lead`초 전부터 `get_theme_time` API를 `--interval` 간격으로 폴링
   (오픈 전엔 "예약가능시간이 아닙니다" 응답 = 화면의 alert)
5. 열리면 `--time` 우선순위대로 빈 슬롯 선택 → STEP 2 이동 → 인원/이름/연락처/필수동의 입력
6. CAPTCHA 풀고 예약하기 → 완료 페이지 확인 후 창 닫으면 종료

## 사용

```sh
cp config.example.toml config.toml   # 이름/전화/인원/기본 지점·테마
uv run --with playwright playwright install chromium   # 최초 1회 (Chrome 있으면 생략 가능)

# 10/29 19:50 (없으면 18:20) — 메모리컴퍼니 오픈 10/23 10:30 에 자동 시도
uv run keyescape.py -d 10-29 -t 19:50,18:20

# 다른 지점/테마, 오픈 시각 직접 지정
uv run keyescape.py -d 2026-11-02 -b 강남점 -T "<테마명>" -t 20:00 --open-at 18:00

# 브라우저 없이 오픈 감지·슬롯만 확인
uv run keyescape.py -d 10-29 --no-browser
```

`--time` 생략 시 가장 이른 빈 시간. 주요 옵션: `--people`, `--interval`(기본 0.1s), `--lead`(5s), `--give-up`(오픈 후 180s).
맥이 잠자기에 들어가지 않도록 `caffeinate -i uv run keyescape.py ...` 로 실행 권장.
