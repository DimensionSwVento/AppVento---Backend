#!/usr/bin/env python3
"""
Prueba de carga del mapa Vento — simula usuarios EXPLORANDO (paneo, zoom, click,
selección de área) con HILOS, mide EN CALIENTE y escala la concurrencia por etapas
HASTA ROMPER. Registro DETALLADO:

  - loadtest-report.txt   → resumen legible: timeline (cada 2s con RAM del server),
                            stats por endpoint, resumen por usuario, punto de rotura.
  - loadtest-requests.csv → HISTÓRICO de CADA petición: t, usuario(hilo), endpoint,
                            ms, ok. (una fila por request)
  - loadtest-users.csv    → por USUARIO(hilo): nº peticiones, errores, avg/p50/p95/max
                            ms, duración.

RAM del server EN VIVO: sondea GET /api/_debug/stats (requiere DEBUG_INFLIGHT=1 en el
backend) → memoria usada/disponible, swap, RSS del worker e in-flight en cada momento.

NO crea usuarios fantasma: reutiliza UN token/cookie real (tu sesión) para todo.

Uso:
  python tools/loadtest.py --base http://localhost:8000 --token "<JWT>"
  python tools/loadtest.py --base http://localhost:3000 --cookie "<cookie>"
Requiere: requests  (pip install requests)
"""
from __future__ import annotations
import argparse
import csv
import random
import subprocess
import threading
import time

try:
    import requests
except ImportError:  # noqa: BLE001
    raise SystemExit("Falta 'requests': pip install requests")

_W, _S, _E, _N = -79.0, -4.2, -67.0, 12.5
_TIMEOUT = 10.0  # s por petición (corto → un endpoint lento FALLA rápido y el user sigue)

_lock = threading.Lock()
_events: list[tuple[float, str, str, float, bool]] = []  # (t, user, endpoint, ms, ok)
_timeline: list[dict] = []
_active = 0
_max_active = 0
_served = 0
_errors = 0
_t0 = 0.0
_broken: set[str] = set()  # endpoints que ya rompieron → se SACAN del flujo y se sigue

# acción del flujo → endpoint (para dropear la acción si su endpoint rompió)
_ACTION_EP = {
    "layers": "POST /tiles/variables-bulk",
    "area": "POST /tiles/area",
    "detail": "GET /tiles/detail",
}
_ACTION_W = {"layers": 6, "area": 2, "detail": 2}


def _now() -> float:
    return time.time() - _t0


def _record(user: str, endpoint: str, ms: float, ok: bool, status: object) -> None:
    global _served, _errors
    with _lock:
        _events.append((round(_now(), 3), user, endpoint, round(ms, 1), ok, status))
        _served += 1
        if not ok:
            _errors += 1


def _req(sess: requests.Session, user: str, method: str, url: str, label: str, **kw) -> None:
    t = time.perf_counter()
    ok = False
    status: object = "ERR"
    try:
        r = sess.request(method, url, timeout=_TIMEOUT, **kw)
        status = r.status_code
        ok = r.status_code < 400
    except Exception as e:  # noqa: BLE001 — timeout/conn reset = fallo (se registra el motivo)
        status = f"ERR:{type(e).__name__}"
        ok = False
    finally:
        _record(user, label, (time.perf_counter() - t) * 1000, ok, status)


def _rand_bbox() -> dict:
    w = random.uniform(_W, _E - 1.0)
    s = random.uniform(_S, _N - 1.0)
    span = random.uniform(0.3, 4.0)
    return {"west": w, "south": s, "east": w + span, "north": s + span}


def _user_flow(user: str, base: str, headers: dict, codes: list[str],
               hexes: list[str], stop: threading.Event) -> None:
    global _active, _max_active
    sess = requests.Session()
    sess.headers.update(headers)
    with _lock:
        _active += 1
        _max_active = max(_max_active, _active)
    try:
        # Carga inicial del mapa (una vez). `excluded` se SACA: tarda ~30s y
        # bloqueaba todo. Si warmup/variables ya rompieron, también se saltan.
        if "GET /tiles/warmup" not in _broken:
            _req(sess, user, "GET", f"{base}/api/tiles/warmup", "GET /tiles/warmup")
        if "GET /tiles/variables" not in _broken:
            _req(sess, user, "GET", f"{base}/api/tiles/variables", "GET /tiles/variables")
        while not stop.is_set():
            # Acciones DISPONIBLES = las que tienen datos y cuyo endpoint NO ha roto
            # (los rotos se SACAN del flujo → se sigue martillando los sanos).
            avail = [a for a in ("layers", "area", "detail")
                     if _ACTION_EP[a] not in _broken
                     and (codes if a == "layers" else hexes)]
            if not avail:
                time.sleep(random.uniform(0.2, 1.0))
                continue
            action = random.choices(avail, weights=[_ACTION_W[a] for a in avail])[0]
            if action == "layers":
                _req(sess, user, "POST", f"{base}/api/tiles/variables-bulk",
                     "POST /tiles/variables-bulk",
                     json={"codes": random.sample(codes, random.randint(1, min(3, len(codes)))),
                           "bbox": _rand_bbox(), "res": random.choice([5, 6, 7, 8])})
            elif action == "area":
                _req(sess, user, "POST", f"{base}/api/tiles/area", "POST /tiles/area",
                     json={"hexes": random.sample(hexes, min(len(hexes), random.randint(20, 300)))})
            elif action == "detail":
                _req(sess, user, "GET", f"{base}/api/tiles/detail/{random.choice(hexes)}",
                     "GET /tiles/detail")
            time.sleep(random.uniform(0.05, 0.4))  # think time
    finally:
        with _lock:
            _active -= 1


def _poll_stats(base: str, headers: dict) -> dict:
    try:
        r = requests.get(f"{base}/api/_debug/stats", headers=headers, timeout=5)
        return r.json() if r.ok else {}
    except Exception:  # noqa: BLE001
        return {}


def _poll_inflight(base: str, headers: dict) -> str:
    """Top peticiones EN VUELO ahora en el server (lo que da /_debug/inflight):
    'path(age_ms)'. Vacío si no está disponible."""
    try:
        r = requests.get(f"{base}/api/_debug/inflight", headers=headers, timeout=5)
        if not r.ok:
            return ""
        items = (r.json().get("requests") or [])[:5]
        return " ".join(f"{i.get('path','?')}({i.get('age_ms','?')}ms)" for i in items)
    except Exception:  # noqa: BLE001
        return ""


def _gunicorn_pids() -> list[str]:
    try:
        out = subprocess.run(["pgrep", "-f", "gunicorn"], capture_output=True, text=True, timeout=5)
        pids = [p for p in out.stdout.split() if p]
        return pids[1:] if len(pids) > 1 else pids  # salta el master (1er pid)
    except Exception:  # noqa: BLE001
        return []


def _pyspy_dump(venv: str, pids: list[str], out_file: str, t: float) -> None:
    """Vuelca el stack de TODOS los hilos de cada worker (lo que hace py-spy dump).
    Requiere py-spy instalado (venv) y permisos (sudo). Si falla, se ignora."""
    bin_ = f"{venv}/bin/py-spy" if venv else "py-spy"
    with open(out_file, "a", encoding="utf-8") as f:
        for pid in pids:
            f.write(f"\n===== t={t:.0f}s  pid={pid} =====\n")
            for cmd in (["sudo", bin_, "dump", "--pid", pid], [bin_, "dump", "--pid", pid]):
                try:
                    r = subprocess.run(cmd, capture_output=True, text=True, timeout=15)
                    f.write(r.stdout or r.stderr)
                    if r.returncode == 0:
                        break
                except Exception as e:  # noqa: BLE001
                    f.write(f"(py-spy falló: {type(e).__name__})\n")


def _monitor(base: str, headers: dict, stop: threading.Event,
             pyspy_file: str | None, venv: str, interval: int = 2) -> None:
    last = 0
    pids = _gunicorn_pids() if pyspy_file else []
    while not stop.is_set():
        time.sleep(interval)
        with _lock:
            active, served = _active, _served
            cut = _now() - interval
            recent = [e for e in _events if e[0] >= cut and e[4]]
        rps = round((served - last) / interval, 1)
        last = served
        lat = sorted(e[3] for e in recent)
        p95 = lat[int(len(lat) * 0.95)] if lat else 0
        st = _poll_stats(base, headers)
        inflight_detail = _poll_inflight(base, headers)
        row = {"t": round(_now(), 1), "active": active, "rps": rps, "p95_ms": round(p95),
               "sys_used_mb": st.get("sys_used_mb", "-"), "sys_avail_mb": st.get("sys_avail_mb", "-"),
               "swap_used_mb": st.get("swap_used_mb", "-"), "inflight": st.get("inflight", "-"),
               "worker_rss_mb": st.get("worker_rss_mb", "-"), "worker_pid": st.get("pid", "-"),
               "stuck": inflight_detail}
        with _lock:
            _timeline.append(row)
        print(f"  t={row['t']:>6}s  activos={active:>4}  rps={rps:>7}  p95={row['p95_ms']:>6}ms  "
              f"RAM_usada={row['sys_used_mb']}MB  disp={row['sys_avail_mb']}MB  "
              f"swap={row['swap_used_mb']}  inflight={row['inflight']}  "
              f"rss_worker={row['worker_rss_mb']}MB(pid {row['worker_pid']})", flush=True)
        if pyspy_file and pids:
            _pyspy_dump(venv, pids, pyspy_file, row["t"])


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * p))]


def _bootstrap(base: str, headers: dict) -> tuple[list[str], list[str]]:
    """Preflight: comprueba conectividad/auth e imprime el motivo si falla. Deriva
    códigos (/variables) y hexes de muestra (/excluded) reales."""
    codes: list[str] = []
    hexes: list[str] = []
    # Preflight claro: si esto falla, TODAS las peticiones fallarán igual.
    try:
        r = requests.get(f"{base}/api/health", headers=headers, timeout=10)
        print(f"  preflight GET /api/health → {r.status_code}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  !! preflight FALLÓ: {type(e).__name__}: {e}", flush=True)
        print(f"  !! Revisa --base ({base}) y que el server esté arriba/accesible.", flush=True)
    try:
        r = requests.get(f"{base}/api/tiles/variables", headers=headers, timeout=30)
        print(f"  bootstrap GET /tiles/variables → {r.status_code}", flush=True)
        if r.ok:
            codes = [v["name"] for v in (r.json().get("variables") or []) if v.get("name")]
        elif r.status_code in (401, 403):
            print("  !! 401/403 → falta/expiró el token. Pasa --token o --cookie de tu sesión.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  !! /tiles/variables FALLÓ: {type(e).__name__}: {e}", flush=True)
    # Hexes de muestra desde /variables-bulk a RES 8 (celdas REALES que existen en la
    # BD → `detail` no da 404). NO de /excluded (tarda ~30s). Se prueban varios bbox
    # CHICOS (0.4°) hasta encontrar uno con datos (muchos caen en mar/sin cobertura).
    if codes:
        for _try in range(5):
            b = _rand_bbox()
            small = {"west": b["west"], "south": b["south"],
                     "east": b["west"] + 0.4, "north": b["south"] + 0.4}
            try:
                r = requests.post(f"{base}/api/tiles/variables-bulk", headers=headers, timeout=30,
                                  json={"codes": codes[:2], "bbox": small, "res": 8})
                if r.ok:
                    seen: set[str] = set()
                    for rows in (r.json() or {}).values():
                        for row in rows or []:
                            h = row.get("h3_index")
                            if h:
                                seen.add(h)
                    if seen:
                        hexes = random.sample(list(seen), min(len(seen), 3000))
                        print(f"  bootstrap POST /tiles/variables-bulk (res8) → {r.status_code}, "
                              f"{len(hexes)} hexes de muestra", flush=True)
                        break
                else:
                    print(f"  bootstrap POST /tiles/variables-bulk → {r.status_code}", flush=True)
                    if r.status_code in (401, 403):
                        break
            except Exception as e:  # noqa: BLE001
                print(f"  !! /tiles/variables-bulk FALLÓ: {type(e).__name__}: {e}", flush=True)
    if not codes and not hexes:
        print("  !! codes=0 y hexes=0 → el bucle no tendrá qué pedir. Arregla base/auth "
              "antes de correr la prueba.", flush=True)
    return codes, hexes


def _write_reports(out_txt: str, cfg: list[str]) -> None:
    base = out_txt.rsplit(".", 1)[0]
    # 1) requests.csv — histórico de CADA petición (ordenado por tiempo), con STATUS
    #    (código HTTP o ERR:<excepción>) → se ve el orden y el resultado de cada una.
    with open(f"{base}-requests.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "usuario", "endpoint", "ms", "ok", "status"])
        with _lock:
            w.writerows(sorted(_events, key=lambda e: e[0]))
    # 2) users.csv + agregado por usuario
    by_user: dict[str, list[tuple[str, float, bool]]] = {}
    with _lock:
        for t, u, ep, ms, ok, _st in _events:
            by_user.setdefault(u, []).append((ep, ms, ok))
    user_rows = []
    for u, evs in by_user.items():
        lat = [e[1] for e in evs]
        errs = sum(1 for e in evs if not e[2])
        user_rows.append({
            "usuario": u, "peticiones": len(evs), "errores": errs,
            "avg_ms": round(sum(lat) / len(lat)) if lat else 0,
            "p50_ms": round(_pct(lat, 0.50)), "p95_ms": round(_pct(lat, 0.95)),
            "max_ms": round(max(lat)) if lat else 0,
        })
    with open(f"{base}-users.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.DictWriter(f, fieldnames=["usuario", "peticiones", "errores", "avg_ms",
                                          "p50_ms", "p95_ms", "max_ms"])
        w.writeheader()
        w.writerows(sorted(user_rows, key=lambda r: -r["p95_ms"]))
    # 3) report.txt — legible
    lines = list(cfg)
    lines.append("\n## Timeline (cada 2s) — activos, rps, p95 y RAM del server")
    lines.append(f"{'t(s)':>6} {'activos':>7} {'rps':>7} {'p95ms':>6} {'RAMus':>6} "
                 f"{'disp':>6} {'swap':>5} {'inflight':>8} {'rssWk':>6}")
    for r in _timeline:
        lines.append(f"{r['t']:>6} {r['active']:>7} {r['rps']:>7} {r['p95_ms']:>6} "
                     f"{r['sys_used_mb']:>6} {r['sys_avail_mb']:>6} {r['swap_used_mb']:>5} "
                     f"{r['inflight']:>8} {r['worker_rss_mb']:>6}")
    # Peticiones EN VUELO (stuck) por momento, si /_debug/inflight está disponible.
    stuck_rows = [(r["t"], r.get("stuck")) for r in _timeline if r.get("stuck")]
    if stuck_rows:
        lines.append("\n## En vuelo (stuck) por momento — path(age)")
        for t, s in stuck_rows:
            lines.append(f"  t={t}s  {s}")
    # por endpoint (+ desglose de STATUS: 200/401/404/500/ERR… → ves la causa)
    by_ep: dict[str, list[tuple[float, bool, object]]] = {}
    status_total: dict[object, int] = {}
    with _lock:
        for _t, _u, ep, ms, ok, st in _events:
            by_ep.setdefault(ep, []).append((ms, ok, st))
            status_total[st] = status_total.get(st, 0) + 1
    lines.append("\n## Status global (código HTTP o ERR:<excepción>)")
    for st, c in sorted(status_total.items(), key=lambda kv: -kv[1]):
        lines.append(f"  {str(st):<20} {c}")
    lines.append("\n## Por endpoint")
    lines.append(f"{'endpoint':<28} {'req':>7} {'err%':>5} {'p50':>6} {'p95':>6} {'p99':>6} {'max':>7}  status")
    for ep, evs in sorted(by_ep.items(), key=lambda kv: -len(kv[1])):
        lat = [e[0] for e in evs]
        errp = round(100 * sum(1 for e in evs if not e[1]) / len(evs), 1)
        sts: dict[object, int] = {}
        for e in evs:
            sts[e[2]] = sts.get(e[2], 0) + 1
        st_str = " ".join(f"{k}:{v}" for k, v in sorted(sts.items(), key=lambda kv: -kv[1]))
        lines.append(f"{ep:<28} {len(evs):>7} {errp:>5} {round(_pct(lat,0.5)):>6} "
                     f"{round(_pct(lat,0.95)):>6} {round(_pct(lat,0.99)):>6} {round(max(lat)):>7}  {st_str}")
    # por usuario (distribución; detalle completo en users.csv)
    reqs_per_user = sorted(r["peticiones"] for r in user_rows)
    lines.append("\n## Por usuario (distribución; detalle completo en *-users.csv)")
    if reqs_per_user:
        lines.append(f"- usuarios totales: {len(user_rows)}")
        lines.append(f"- peticiones/usuario: min {reqs_per_user[0]} · "
                     f"mediana {reqs_per_user[len(reqs_per_user)//2]} · max {reqs_per_user[-1]}")
        slow = sorted(user_rows, key=lambda r: -r["p95_ms"])[:5]
        lines.append("- 5 usuarios más lentos (p95 ms): " +
                     ", ".join(f"{r['usuario']}={r['p95_ms']}" for r in slow))
    # resumen (el límite POR ENDPOINT se añade a `cfg` desde main)
    lines.append("\n## Resumen")
    lines.append(f"- Pico de usuarios concurrentes: {_max_active}")
    lines.append(f"- Peticiones totales: {_served}  (errores: {_errors})")
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReportes: {out_txt} · {base}-requests.csv · {base}-users.csv", flush=True)


def main() -> None:
    global _t0
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--token")
    ap.add_argument("--cookie")
    ap.add_argument("--stages", default="10,25,50,100,200,400,800")
    ap.add_argument("--stage-secs", type=int, default=20)
    ap.add_argument("--break-err", type=float, default=25.0)
    ap.add_argument("--break-p95", type=float, default=8000.0)
    ap.add_argument("--req-timeout", type=float, default=10.0,
                    help="timeout por petición (s); corto → un endpoint lento falla rápido y sigue")
    ap.add_argument("--out", default="loadtest-report.txt")
    ap.add_argument("--pyspy", action="store_true",
                    help="volca stacks de hilos (py-spy dump) de los workers gunicorn cada 2s")
    ap.add_argument("--venv", default="", help="ruta del venv (para hallar py-spy)")
    args = ap.parse_args()

    global _TIMEOUT
    _TIMEOUT = args.req_timeout

    headers: dict = {}
    if args.token:
        headers["Authorization"] = f"Bearer {args.token}"
    if args.cookie:
        headers["Cookie"] = args.cookie

    _t0 = time.time()
    codes, hexes = _bootstrap(args.base, headers)
    stages = [int(x) for x in args.stages.split(",") if x.strip()]
    cfg = [
        f"# Prueba de carga Vento — {args.base}",
        f"# auth: {'token' if args.token else 'cookie' if args.cookie else 'NINGUNO'} · "
        f"codes={len(codes)} hexes-muestra={len(hexes)}",
        f"# corte: error>{args.break_err}% o p95>{args.break_p95:.0f}ms · "
        f"RAM del server requiere DEBUG_INFLIGHT=1",
    ]
    for c in cfg:
        print(c, flush=True)

    mon_stop = threading.Event()
    pyspy_file = f"{args.out.rsplit('.', 1)[0]}-pyspy.txt" if args.pyspy else None
    threading.Thread(target=_monitor,
                     args=(args.base, headers, mon_stop, pyspy_file, args.venv),
                     daemon=True).start()

    limits: dict[str, int] = {}  # endpoint → nº usuarios donde ROMPIÓ (1ª vez)
    stage_summ: list[str] = []
    for users in stages:
        stop = threading.Event()
        threads = [threading.Thread(target=_user_flow,
                                    args=(f"{users}#{i}", args.base, headers, codes, hexes, stop),
                                    daemon=True) for i in range(users)]
        with _lock:
            t_mark = _now()
        for t in threads:
            t.start()
            time.sleep(0.005)
        time.sleep(args.stage_secs)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        with _lock:
            evs = [e for e in _events if e[0] >= t_mark]
        # Stats POR ENDPOINT en esta etapa → marca el LÍMITE de cada uno (sin parar
        # la prueba: si uno rompe, se anota y se SIGUE con los demás).
        ep_stage: dict[str, list] = {}
        for e in evs:
            ep_stage.setdefault(e[2], []).append(e)
        parts = []
        for ep in sorted(ep_stage):
            es = ep_stage[ep]
            lat = [x[3] for x in es if x[4]]
            errp = round(100 * sum(1 for x in es if not x[4]) / len(es), 1)
            p95 = round(_pct(lat, 0.95))
            broke = errp >= args.break_err or p95 >= args.break_p95
            if ep not in limits and broke:
                limits[ep] = users
            short = ep.split()[-1]
            parts.append(f"{short}{'⚠' if broke else ''}(err{errp}% p95{p95})")
        line = f"etapa {users:>4} usuarios: " + "  ".join(parts)
        print("  >> " + line, flush=True)
        stage_summ.append(line)
        # DROP de los rotos: se SACAN del flujo y se SIGUE con los sanos (sin parar).
        newly = [ep for ep in limits if ep not in _broken]
        _broken.update(limits.keys())
        if newly:
            print("  (sacados del flujo, se sigue con los demás: " + ", ".join(newly) + ")", flush=True)
        # Solo se detiene si YA NO queda ningún endpoint sano que probar.
        all_eps = {"GET /tiles/warmup", "GET /tiles/variables",
                   "POST /tiles/variables-bulk", "POST /tiles/area", "GET /tiles/detail"}
        if all_eps <= _broken:
            print("\n>>> todos los endpoints rompieron; se detiene.", flush=True)
            break

    mon_stop.set()
    time.sleep(0.2)
    cfg.append("\n## Etapas (⚠ = rompió en esa etapa)")
    cfg.extend(stage_summ)
    cfg.append("\n## Límite por endpoint (nº usuarios concurrentes donde rompió)")
    seen_eps = set()
    with _lock:
        for e in _events:
            seen_eps.add(e[2])
    for ep in sorted(seen_eps):
        cfg.append(f"  {ep:<28} → {('ROMPIÓ en ' + str(limits[ep])) if ep in limits else 'estable (no rompió)'}")
    _write_reports(args.out, cfg)


if __name__ == "__main__":
    main()
