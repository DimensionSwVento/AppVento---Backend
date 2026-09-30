#!/usr/bin/env python3
"""
Prueba de carga del mapa Vento — simula usuarios EXPLORANDO (paneo, zoom in/out,
click, selección de área) con HILOS, mide el rendimiento EN CALIENTE y escala la
concurrencia por etapas HASTA ROMPER. Escribe un TXT con las mediciones.

NO crea usuarios fantasma: reutiliza UN token real (tu sesión) para todas las
peticiones. Sáltate el auth pasando el token de tu sesión (DevTools → Network →
cualquier request a /api → cabecera Authorization, o la cookie de NextAuth).

Uso:
  python tools/loadtest.py --base http://localhost:8000 --token "<JWT>"      # backend directo
  python tools/loadtest.py --base http://localhost:3000 --cookie "<cookie>"  # a través del front
  # sin auth (solo si el backend tiene bypass o para probar endpoints abiertos):
  python tools/loadtest.py --base http://localhost:8000

Requiere: requests  (pip install requests)
"""
from __future__ import annotations
import argparse
import random
import threading
import time

try:
    import requests
except ImportError:  # noqa: BLE001
    raise SystemExit("Falta 'requests': pip install requests")

# ── Bounds aprox de Colombia (para bboxes de exploración aleatorios) ────────────
_W, _S, _E, _N = -79.0, -4.2, -67.0, 12.5

# ── Estado compartido (thread-safe) ────────────────────────────────────────────
_lock = threading.Lock()
_samples: list[tuple[str, float, bool]] = []  # (endpoint, ms, ok)
_active = 0          # usuarios concurrentes AHORA
_max_active = 0      # pico de concurrencia
_served = 0          # peticiones completadas
_errors = 0          # peticiones fallidas


def _record(endpoint: str, ms: float, ok: bool) -> None:
    global _served, _errors
    with _lock:
        _samples.append((endpoint, ms, ok))
        _served += 1
        if not ok:
            _errors += 1


def _req(sess: requests.Session, method: str, url: str, **kw) -> bool:
    t0 = time.perf_counter()
    ok = False
    try:
        r = sess.request(method, url, timeout=30, **kw)
        ok = r.status_code < 400
        return ok
    except Exception:  # noqa: BLE001 — cuenta como fallo (timeout/conn reset)
        return False
    finally:
        _record(url.split("?")[0].rsplit("/", 1)[0], (time.perf_counter() - t0) * 1000, ok)


def _rand_bbox() -> dict:
    w = random.uniform(_W, _E - 1.0)
    s = random.uniform(_S, _N - 1.0)
    span = random.uniform(0.3, 4.0)  # desde "acercado" a "alejado"
    return {"west": w, "south": s, "east": w + span, "north": s + span}


def _user_flow(base: str, headers: dict, codes: list[str], hexes: list[str],
               stop: threading.Event) -> None:
    """Un usuario: abre el mapa y EXPLORA en bucle (no un flujo lineal)."""
    global _active, _max_active
    sess = requests.Session()
    sess.headers.update(headers)
    with _lock:
        _active += 1
        _max_active = max(_max_active, _active)
    try:
        # Carga inicial del mapa (una vez).
        _req(sess, "GET", f"{base}/api/tiles/warmup")
        _req(sess, "GET", f"{base}/api/tiles/excluded")
        _req(sess, "GET", f"{base}/api/tiles/variables")
        while not stop.is_set():
            # Acción aleatoria ponderada = exploración real (mucho paneo/zoom de capas,
            # algo de selección de área y clicks de detalle, alguna pausa).
            action = random.choices(
                ["layers", "area", "detail", "idle"], weights=[6, 2, 2, 1]
            )[0]
            if action == "layers" and codes:
                n = random.randint(1, min(3, len(codes)))
                _req(sess, "POST", f"{base}/api/tiles/variables-bulk",
                     json={"codes": random.sample(codes, n),
                           "bbox": _rand_bbox(),
                           "res": random.choice([5, 6, 7, 8])})
            elif action == "area" and hexes:
                k = min(len(hexes), random.randint(20, 300))
                _req(sess, "POST", f"{base}/api/tiles/area",
                     json={"hexes": random.sample(hexes, k)})
            elif action == "detail" and hexes:
                _req(sess, "GET", f"{base}/api/tiles/detail/{random.choice(hexes)}")
            else:
                time.sleep(random.uniform(0.2, 1.2))
            time.sleep(random.uniform(0.05, 0.4))  # "think time" entre acciones
    finally:
        with _lock:
            _active -= 1


def _bootstrap(base: str, headers: dict) -> tuple[list[str], list[str]]:
    """Deriva códigos (de /variables) y hexes de muestra (de /excluded) reales."""
    codes: list[str] = []
    hexes: list[str] = []
    try:
        r = requests.get(f"{base}/api/tiles/variables", headers=headers, timeout=30)
        if r.ok:
            codes = [v["name"] for v in (r.json().get("variables") or []) if v.get("name")]
    except Exception:  # noqa: BLE001
        pass
    try:
        r = requests.get(f"{base}/api/tiles/excluded", headers=headers, timeout=60)
        if r.ok:
            cells = r.json().get("cells") or []
            hexes = random.sample(cells, min(len(cells), 2000)) if cells else []
    except Exception:  # noqa: BLE001
        pass
    return codes, hexes


def _snapshot_and_reset() -> dict:
    with _lock:
        s = list(_samples)
        _samples.clear()
    if not s:
        return {"n": 0}
    ms = sorted(x[1] for x in s)
    ok = sum(1 for x in s if x[2])
    return {
        "n": len(s),
        "ok": ok,
        "err_rate": round(100 * (len(s) - ok) / len(s), 1),
        "p50": round(ms[len(ms) // 2]),
        "p95": round(ms[int(len(ms) * 0.95)]),
        "p99": round(ms[int(len(ms) * 0.99)]),
        "max": round(ms[-1]),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True, help="URL base (backend :8000 o front :3000)")
    ap.add_argument("--token", help="JWT de tu sesión (Authorization: Bearer ...)")
    ap.add_argument("--cookie", help="Cookie de sesión (para el front)")
    ap.add_argument("--stages", default="10,25,50,100,200,400,800",
                    help="Niveles de usuarios concurrentes por etapa")
    ap.add_argument("--stage-secs", type=int, default=20, help="Duración por etapa (s)")
    ap.add_argument("--break-err", type=float, default=25.0, help="%% error que corta la prueba")
    ap.add_argument("--break-p95", type=float, default=8000.0, help="p95 ms que corta la prueba")
    ap.add_argument("--out", default="loadtest-report.txt")
    args = ap.parse_args()

    headers: dict = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    if args.cookie:
        headers["Cookie"] = args.cookie

    codes, hexes = _bootstrap(args.base, headers)
    stages = [int(x) for x in args.stages.split(",") if x.strip()]
    lines: list[str] = []

    def log(msg: str) -> None:
        print(msg, flush=True)
        lines.append(msg)

    log(f"# Prueba de carga Vento — {args.base}")
    log(f"# auth: {'token' if args.token else 'cookie' if args.cookie else 'NINGUNO'} · "
        f"codes={len(codes)} hexes-muestra={len(hexes)} · hilos = usuarios concurrentes")
    log(f"# corte: error>{args.break_err}% o p95>{args.break_p95:.0f}ms")
    log("")
    log(f"{'etapa(usuarios)':>16} | {'req':>6} {'err%':>5} {'p50':>6} {'p95':>6} {'p99':>6} {'max':>7} {'pico':>5}")

    broke_at: int | None = None
    for users in stages:
        stop = threading.Event()
        threads = [
            threading.Thread(target=_user_flow, args=(args.base, headers, codes, hexes, stop),
                             daemon=True)
            for _ in range(users)
        ]
        _snapshot_and_reset()  # descarta el arranque
        for t in threads:
            t.start()
            time.sleep(0.01)  # rampa suave de arranque
        # ── monitor EN CALIENTE ──
        t_end = time.time() + args.stage_secs
        while time.time() < t_end:
            time.sleep(2)
            with _lock:
                cur_active, cur_served, cur_max = _active, _served, _max_active
            print(f"  … activos={cur_active:>4}  servidos(total)={cur_served:>7}  "
                  f"pico={cur_max:>4}", flush=True)
        st = _snapshot_and_reset()
        stop.set()
        for t in threads:
            t.join(timeout=5)

        rps = round(st.get("n", 0) / args.stage_secs, 1)
        log(f"{users:>16} | {st.get('n',0):>6} {st.get('err_rate',0):>5} "
            f"{st.get('p50','-'):>6} {st.get('p95','-'):>6} {st.get('p99','-'):>6} "
            f"{st.get('max','-'):>7} {_max_active:>5}   ({rps}/s)")
        if st.get("err_rate", 0) >= args.break_err or (st.get("p95") or 0) >= args.break_p95:
            broke_at = users
            log(f"\n>>> ROMPIÓ en {users} usuarios concurrentes "
                f"(error {st.get('err_rate')}% · p95 {st.get('p95')}ms). Se detiene.")
            break

    log("")
    log("## Resumen")
    log(f"- Pico de usuarios concurrentes atendidos: {_max_active}")
    log(f"- Total de peticiones servidas: {_served}  (errores: {_errors})")
    if broke_at:
        idx = stages.index(broke_at)
        ok_level = stages[idx - 1] if idx > 0 else 0
        log(f"- Última etapa ESTABLE: {ok_level} usuarios · Rompió en: {broke_at}")
    else:
        log(f"- No rompió hasta {stages[-1]} usuarios (sube --stages para hallar el techo).")

    with open(args.out, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReporte escrito en {args.out}", flush=True)


if __name__ == "__main__":
    main()
