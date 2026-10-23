# /// script
# requires-python = ">=3.11"
# dependencies = ["httpx>=0.27", "playwright>=1.47", "truststore>=0.9"]
# ///
"""키이스케이프 예약 오픈 스나이퍼.

예약 오픈 시각(예약일 6일 전, 지점별 오픈시간, KST)에 맞춰 시간표 API를 빠르게 폴링하다가
열리는 순간 브라우저로 STEP 2(정보 입력)까지 진행한다. reCAPTCHA와 최종 '예약하기'는 사용자가 직접.
"""

from __future__ import annotations

import argparse
import difflib
import re
import sys
import time
import tomllib
from datetime import date, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import httpx
import ssl
import truststore

KST = ZoneInfo("Asia/Seoul")
BASE = "https://www.keyescape.com"
API = f"{BASE}/controller/run_proc.php"
UA = (
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/130.0.0.0 Safari/537.36"
)
CONFIG_PATH = Path(__file__).with_name("config.toml")

# 사이트 select#zizum 의 value. 이름은 사이트 표기 그대로.
BRANCHES = {
    "에버랜드": 26,
    "후즈데어": 23,
    "STATION": 22,
    "LOG_IN 1": 19,
    "LOG_IN 2": 20,
    "메모리컴퍼니": 18,
    "우주라이크": 16,
    "더오름": 14,
    "강남점": 3,
    "홍대점": 10,
    "부산점": 9,
    "전주점": 7,
    "무비무드": 25,
    "무비무드 전주": 29,
}
BRANCH_ALIASES = {"강남 더오름": "더오름", "강남더오름": "더오름", "강남": "강남점", "홍대": "홍대점",
                  "부산": "부산점", "전주": "전주점", "login1": "LOG_IN 1", "login2": "LOG_IN 2"}
# 지점별 예약 오픈 시간 (KST). 에버랜드/무비무드 전주는 안내가 없어 --open-at 필요.
OPEN_TIMES = {
    "LOG_IN 1": "10:00", "LOG_IN 2": "10:00", "더오름": "10:00", "우주라이크": "10:00",
    "메모리컴퍼니": "10:30", "후즈데어": "11:00", "STATION": "11:30", "무비무드": "13:30",
    "강남점": "18:00", "부산점": "18:00", "전주점": "18:00", "홍대점": "20:00",
}
OPEN_DAYS_BEFORE = 6

DEFAULTS = {
    "branch": "메모리컴퍼니",
    "theme": "FILM BY EDDY",
    "name": "",
    "phone": "",
    "people": 2,
}


def log(msg: str) -> None:
    now = datetime.now(KST).strftime("%H:%M:%S.%f")[:-3]
    print(f"[{now}] {msg}", flush=True)


def norm(s: str) -> str:
    return re.sub(r"[^0-9a-z가-힣]", "", s.lower())


def best_match(query: str, candidates: list[str], what: str) -> str:
    by_norm = {norm(c): c for c in candidates}
    q = norm(query)
    if q in by_norm:
        return by_norm[q]
    hits = [c for n, c in by_norm.items() if q and (q in n or n in q)]
    if len(hits) == 1:
        return hits[0]
    close = difflib.get_close_matches(q, list(by_norm), n=1, cutoff=0.6)
    if close:
        return by_norm[close[0]]
    sys.exit(f"{what} '{query}' 를 찾을 수 없음. 후보: {', '.join(candidates)}")


class Api:
    def __init__(self, referer: str):
        self.client = httpx.Client(
            verify=truststore.SSLContext(ssl.PROTOCOL_TLS_CLIENT),
            timeout=httpx.Timeout(3.0),
            headers={"User-Agent": UA, "Referer": referer, "X-Requested-With": "XMLHttpRequest"},
        )

    def post(self, **data) -> dict:
        r = self.client.post(API, data=data)
        r.raise_for_status()
        return r.json()

    def themes(self, zizum_num: int) -> list[dict]:
        j = self.post(t="get_theme_info_list", zizum_num=zizum_num)
        if not j.get("status"):
            sys.exit(f"테마 목록 조회 실패: {j.get('msg')}")
        return j["data"]

    def times(self, day: str, zizum_num: int, theme_num: int) -> dict:
        return self.post(t="get_theme_time", date=day, zizumNum=zizum_num, themeNum=theme_num)

    def server_offset(self) -> float:
        """서버 Date 헤더 기준 (서버 - 로컬) 초. 1초 해상도라 대략치."""
        t0 = time.time()
        r = self.client.head(BASE + "/")
        t1 = time.time()
        srv = datetime.strptime(r.headers["date"], "%a, %d %b %Y %H:%M:%S GMT").replace(tzinfo=ZoneInfo("UTC"))
        return srv.timestamp() + 0.5 - (t0 + t1) / 2


def parse_args() -> argparse.Namespace:
    cfg = dict(DEFAULTS)
    if CONFIG_PATH.exists():
        cfg.update(tomllib.loads(CONFIG_PATH.read_text()))

    p = argparse.ArgumentParser(description="키이스케이프 예약 오픈 스나이퍼")
    p.add_argument("--date", "-d", required=True, help="이용 날짜 YYYY-MM-DD (또는 MM-DD)")
    p.add_argument("--time", "-t", action="append", default=[],
                   help="원하는 시작 시간 HH:MM. 여러 번/쉼표로 우선순위 지정. 생략 시 가장 이른 빈 시간")
    p.add_argument("--branch", "-b", default=cfg["branch"], help=f"지점 (기본 {cfg['branch']})")
    p.add_argument("--theme", "-T", default=cfg["theme"], help=f"테마 (기본 {cfg['theme']})")
    p.add_argument("--open-at", help="예약 오픈 시각 override. 'HH:MM' 또는 'YYYY-MM-DD HH:MM' (KST)")
    p.add_argument("--name", default=cfg["name"], help="예약자 이름")
    p.add_argument("--phone", default=cfg["phone"], help="연락처 010-XXXX-XXXX")
    p.add_argument("--people", "-p", type=int, default=cfg["people"], help="인원")
    p.add_argument("--interval", type=float, default=0.1, help="오픈 직전/직후 폴링 간격(초). 기본 0.1")
    p.add_argument("--lead", type=float, default=5.0, help="오픈 몇 초 전부터 빠르게 폴링할지. 기본 5")
    p.add_argument("--give-up", type=float, default=180.0, help="오픈 후 몇 초 동안 시도할지. 기본 180")
    p.add_argument("--no-browser", action="store_true", help="브라우저 없이 오픈 감지/슬롯 확인만")
    args = p.parse_args()

    args.time = [t.strip() for chunk in args.time for t in chunk.split(",") if t.strip()]
    for t in args.time:
        if not re.fullmatch(r"\d{1,2}:\d{2}", t):
            p.error(f"--time 형식 오류: {t}")
    args.time = [f"{int(t.split(':')[0]):02d}:{t.split(':')[1]}" for t in args.time]

    today = datetime.now(KST).date()
    d = args.date
    if re.fullmatch(r"\d{1,2}-\d{1,2}", d):
        m, dd = map(int, d.split("-"))
        cand = date(today.year, m, dd)
        args.date = cand if cand >= today else date(today.year + 1, m, dd)
    else:
        args.date = date.fromisoformat(d)

    digits = re.sub(r"\D", "", args.phone)
    if not args.no_browser and not re.fullmatch(r"010\d{8}", digits):
        p.error("--phone 이 필요함 (010XXXXXXXX). config.toml 에 넣어두면 편함")
    args.phone = digits
    if not args.no_browser and not args.name:
        p.error("--name 이 필요함. config.toml 에 넣어두면 편함")
    return args


def resolve_open_at(args, branch: str) -> datetime:
    if args.open_at:
        s = args.open_at.strip()
        if re.fullmatch(r"\d{1,2}:\d{2}", s):
            day = args.date - timedelta(days=OPEN_DAYS_BEFORE)
            return datetime.combine(day, datetime.strptime(s, "%H:%M").time(), KST)
        return datetime.strptime(s, "%Y-%m-%d %H:%M").replace(tzinfo=KST)
    if branch not in OPEN_TIMES:
        sys.exit(f"{branch} 의 오픈 시간을 모름. --open-at HH:MM 로 지정해줘")
    day = args.date - timedelta(days=OPEN_DAYS_BEFORE)
    return datetime.combine(day, datetime.strptime(OPEN_TIMES[branch], "%H:%M").time(), KST)


def pick_slot(slots: list[dict], wanted: list[str]) -> dict | None:
    free = {f"{s['hh']}:{s['mm']}": s for s in slots if s.get("enable") == "Y"}
    if not wanted:
        return free[min(free)] if free else None
    for t in wanted:
        if t in free:
            return free[t]
    return None


def fmt_slots(slots: list[dict]) -> str:
    return " ".join(f"{s['hh']}:{s['mm']}{'' if s.get('enable') == 'Y' else '(X)'}" for s in slots)


def sleep_until(target_ts: float) -> None:
    while (left := target_ts - time.time()) > 0:
        time.sleep(min(left, 30 if left > 60 else 0.5 if left > 2 else 0.01))


def wait_for_open(api: Api, args, zizum: int, theme_num: int, open_at: datetime, offset: float) -> tuple[dict, list[dict]]:
    """오픈 감지 후 원하는 슬롯을 반환. 오픈 전엔 서버가 status=false(+오픈 전 메시지)를 준다."""
    day = args.date.isoformat()
    open_local = open_at.timestamp() - offset  # 로컬 시계 기준 오픈 시각
    start = open_local - args.lead
    if time.time() < start:
        log(f"오픈 {open_at:%m-%d %H:%M} KST 까지 {timedelta(seconds=int(start - time.time() + args.lead))} 남음. 대기…")
        # 대기 중 세션 유지 겸 1분 전 한 번 체크
        sleep_until(start - 60)
        if time.time() < start:
            j = api.times(day, zizum, theme_num)
            log(f"사전 체크: {j.get('msg') or 'status=' + str(j.get('status'))}")
            sleep_until(start)
    log(f"폴링 시작 (interval {args.interval}s)")
    deadline = max(open_local, time.time()) + args.give_up
    last_msg, n = None, 0
    while time.time() < deadline:
        n += 1
        try:
            j = api.times(day, zizum, theme_num)
        except (httpx.HTTPError, ValueError) as e:
            log(f"요청 오류: {e!r}")
            time.sleep(args.interval)
            continue
        if j.get("status"):
            slots = j.get("data") or []
            slot = pick_slot(slots, args.time)
            if slot:
                log(f"오픈 감지 ({n}회) → {slot['hh']}:{slot['mm']} (num={slot['num']}) 선택")
                return slot, slots
            if n == 1 or last_msg != "open":
                log(f"열렸지만 원하는 빈 시간 없음: {fmt_slots(slots)}")
            last_msg = "open"
        else:
            msg = j.get("msg", "")
            if msg != last_msg:
                log(f"아직: {msg}")
                last_msg = msg
        time.sleep(args.interval)
    sys.exit("시간 초과: 원하는 슬롯을 잡지 못함")


def fill_in_browser(args, zizum: int, theme: dict, slot: dict, page) -> None:
    """미리 띄워둔 reservation1 페이지에서 STEP1 폼을 채워 제출 → STEP2 입력."""
    revtime = f"{slot['hh']}:{slot['mm']}"
    page.evaluate(
        """([z, tn, tin, d, ttn, rt, name]) => {
            const f = document.getElementById('form');
            const set = (k, v) => { f.querySelector(`input[name=${k}]`).value = v; };
            set('zizumNum', z); set('themeNum', tn); set('themeInfoNum', tin); set('revDays', d);
            set('themeTimeNum', ttn); set('revTimes', rt); set('themeName', name);
            f.submit();
        }""",
        [str(zizum), str(theme["theme_num"]), str(theme["info_num"]), args.date.isoformat(),
         str(slot["num"]), revtime, theme["info_name"]],
    )
    page.wait_for_selector("#name_input", timeout=15000)
    # 인원 변경 시 금액을 갱신하는 jQuery 핸들러는 페이지 맨 끝 스크립트에서 바인딩된다
    page.wait_for_function("() => window.jQuery && typeof phoneNumberCheck === 'function'", timeout=15000)
    log("STEP 2 도착, 정보 입력 중")

    people = page.locator("#person option").evaluate_all("os => os.map(o => o.value)")
    want = str(args.people)
    if want not in people:
        log(f"인원 {want}명 선택 불가 (가능: {people}). 가장 가까운 값으로")
        want = min(people, key=lambda v: abs(int(v) - args.people))
    # 입력값을 채우는 함수를 페이지에 심어 두고, [예약하기] 클릭 시 사이트 검증보다 먼저 다시 채운다.
    # (CAPTCHA 푸는 동안 연락처 칸 등을 실수로 건드려도 올바른 값으로 제출되도록)
    page.evaluate(
        """([person, name, m2, m3]) => {
            window.__ekFill = () => {
                const q = s => document.querySelector(s);
                if (q('#person').value !== person) jQuery('#person').val(person).trigger('change');
                q('#name_input').value = name;
                q('input[name=mobile2]').value = m2;
                q('input[name=mobile3]').value = m3;
                // 체크박스는 커스텀 UI(span.ui) 뒤에 숨어 있어 직접 체크
                ['agree_1', 'agree_2'].forEach(n => { q(`input[name=${n}]`).checked = true; });
            };
            window.addEventListener('click', e => {
                if (e.target.closest && e.target.closest('.submit')) window.__ekFill();
            }, true);
            window.__ekFill();
        }""",
        [want, args.name, args.phone[3:7], args.phone[7:]],
    )
    page.locator("#captcha").scroll_into_view_if_needed()
    page.bring_to_front()
    log(f"입력 완료: {theme['info_name']} {args.date} {revtime} / {args.name} / {want}명")
    print("\n>>> 브라우저에서 '로봇이 아닙니다' 체크 후 [예약하기] 를 누르세요. 완료 후 창을 닫으면 종료됩니다.\n", flush=True)


def main() -> None:
    args = parse_args()
    branch = BRANCH_ALIASES.get(args.branch.strip(), None) or best_match(args.branch, list(BRANCHES), "지점")
    zizum = BRANCHES[branch]

    probe = Api(f"{BASE}/reservation1.php")
    themes = probe.themes(zizum)
    theme_name = best_match(args.theme, [t["info_name"] for t in themes], "테마")
    theme = next(t for t in themes if t["info_name"] == theme_name)
    referer = (f"{BASE}/reservation1.php?zizum_num={zizum}&theme_num={theme['theme_num']}"
               f"&theme_info_num={theme['info_num']}")
    api = Api(referer)

    open_at = resolve_open_at(args, branch)
    try:
        offset = api.server_offset()
    except Exception:
        offset = 0.0
    log(f"{branch} / {theme_name} / {args.date} {','.join(args.time) or '(가장 이른 빈 시간)'} "
        f"/ 오픈 {open_at:%Y-%m-%d %H:%M} KST / 서버시계 오차 {offset:+.1f}s")

    if args.no_browser:
        slot, _ = wait_for_open(api, args, zizum, int(theme["theme_num"]), open_at, offset)
        return

    from playwright.sync_api import sync_playwright

    with sync_playwright() as pw:
        try:
            browser = pw.chromium.launch(channel="chrome", headless=False)
        except Exception:
            browser = pw.chromium.launch(headless=False)
        ctx = browser.new_context(user_agent=UA, locale="ko-KR", timezone_id="Asia/Seoul",
                                  viewport={"width": 1280, "height": 900})
        # 개발자도구 감지 스크립트 차단 (자동화 탐지 오작동 방지)
        ctx.route(re.compile(r"devtools-detector"), lambda r: r.abort())
        page = ctx.new_page()
        manual = False

        def on_dialog(d) -> None:
            # 자동 진행 중엔 바로 닫고, 사용자 단계에선 alert를 그대로 띄워 직접 확인하게 한다
            if not manual:
                log(f"alert: {d.message}")
                d.accept()
                return
            log(f"\a alert: {d.message}")
            print(f"\n!!! 브라우저 알림: {d.message}  (브라우저에서 [확인] 후 다시 시도)\n", flush=True)

        page.on("dialog", on_dialog)

        def warm() -> None:
            page.goto(referer, wait_until="domcontentloaded")

        warm()
        slot = None
        # 오픈 직전에 페이지를 새로 열어 세션을 신선하게 유지
        lead_ts = open_at.timestamp() - offset - args.lead - 20
        if time.time() < lead_ts:
            log("브라우저 준비 완료. 오픈 20초 전에 페이지를 새로고침합니다")
            sleep_until(lead_ts)
            warm()
        slot, _ = wait_for_open(api, args, zizum, int(theme["theme_num"]), open_at, offset)
        fill_in_browser(args, zizum, theme, slot, page)
        manual = True

        try:
            page.wait_for_url(re.compile(r"reservation3\.php"), timeout=0)
            log(f"예약 완료 페이지 도착: {page.url}")
            page.wait_for_event("close", timeout=0)
        except Exception:
            pass
        finally:
            try:
                browser.close()
            except Exception:
                pass


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n중단됨")
