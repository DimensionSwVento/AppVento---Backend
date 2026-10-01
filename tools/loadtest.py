#!/usr/bin/env python3
"""
Prueba de carga UNITARIA por endpoint del mapa Vento. Cada endpoint se prueba
AISLADO: su propia rampa de usuarios concurrentes (hilos) hasta 1000, buscando SU
límite (donde err%/p95 cruzan el umbral). Así un endpoint lento NO arrastra a los
demás. Mide en caliente y escribe reporte + CSV.

Salidas:
  - loadtest-report.txt   → por endpoint: tabla de la rampa (users→req/err/p50/p95/rps)
                            + su LÍMITE; timeline RAM; resumen de límites.
  - loadtest-requests.csv → cada petición: t, endpoint, ms, ok, status.

RAM del server EN VIVO: sondea GET /api/_debug/stats (requiere DEBUG_INFLIGHT=1).
NO crea usuarios fantasma: reutiliza UN token/cookie real (tu sesión).

DIAGNÓSTICO BD (opcional, `--db-url`): aísla si rompió la QUERY o la APP.
  - En cada endpoint que ROMPE corre `EXPLAIN (ANALYZE, BUFFERS)` de SU consulta
    real, SIN carga → si la query sola ya es lenta (Seq Scan / lee disco) el cuello
    es la BD; si la query sola es rápida, el cuello es la app/concurrencia.
  - El timeline suma salud de la BD (conexiones activas/en espera, cache-hit%,
    temp spill) → ver si la BD se satura durante la carga.
  - ⚠ Córrelo DESDE el server (o por túnel SSH): el 5432 no suele estar público.
    El swap del HOST de la BD: si la BD está en el MISMO server que la app, el
    'swap' del timeline (de /_debug/stats) YA lo incluye; si es server aparte,
    mira cache-hit% y temp_mb como proxy de presión de RAM.

Uso:
  python tools/loadtest.py --base "https://TU_DOMINIO" --cookie "__Secure-authjs.session-token=..."
  python tools/loadtest.py --base http://localhost:8000 --token "<JWT>" \
      --db-url "postgresql://USUARIO:CLAVE@HOST_BD:5432/NOMBRE_BD"
Requiere: requests  (pip install requests) · BD: psycopg o psycopg2 (opcional)
"""
from __future__ import annotations
import argparse
import csv
import random
import threading
import time

try:
    import requests
except ImportError:  # noqa: BLE001
    raise SystemExit("Falta 'requests': pip install requests")

# Pila de hilo más chica → 1000 hilos no revientan la RAM del CLIENTE (~256MB/1000).
try:
    threading.stack_size(256 * 1024)
except (ValueError, RuntimeError):
    pass

_W, _S, _E, _N = -79.0, -4.2, -67.0, 12.5
_TIMEOUT = 10.0

_lock = threading.Lock()
_events: list[tuple[float, str, float, bool, object]] = []  # (t, endpoint, ms, ok, status)
_timeline: list[dict] = []
_active = 0
_max_active = 0
_served = 0
_errors = 0
_t0 = 0.0


def _now() -> float:
    return time.time() - _t0


def _record(endpoint: str, ms: float, ok: bool, status: object) -> None:
    global _served, _errors
    with _lock:
        _events.append((round(_now(), 3), endpoint, round(ms, 1), ok, status))
        _served += 1
        if not ok:
            _errors += 1


def _req(sess: requests.Session, method: str, url: str, label: str, **kw) -> None:
    t = time.perf_counter()
    ok = False
    status: object = "ERR"
    try:
        r = sess.request(method, url, timeout=_TIMEOUT, **kw)
        status = r.status_code
        ok = r.status_code < 400
    except Exception as e:  # noqa: BLE001
        status = f"ERR:{type(e).__name__}"
        ok = False
    finally:
        _record(label, (time.perf_counter() - t) * 1000, ok, status)


def _rand_bbox(span_min: float = 0.3, span_max: float = 4.0) -> dict:
    w = random.uniform(_W, _E - 1.0)
    s = random.uniform(_S, _N - 1.0)
    span = random.uniform(span_min, span_max)
    return {"west": w, "south": s, "east": w + span, "north": s + span}


# ── PROBES: una petición a UN endpoint ──────────────────────────────────────────
def _p_warmup(sess, base, codes, hexes):
    _req(sess, "GET", f"{base}/api/tiles/warmup", "GET /tiles/warmup")


def _p_variables(sess, base, codes, hexes):
    _req(sess, "GET", f"{base}/api/tiles/variables", "GET /tiles/variables")


def _p_bulk(sess, base, codes, hexes):
    _req(sess, "POST", f"{base}/api/tiles/variables-bulk", "POST /tiles/variables-bulk",
         json={"codes": random.sample(codes, random.randint(1, min(3, len(codes)))),
               "bbox": _rand_bbox(), "res": random.choice([5, 6, 7, 8])})


def _p_area(sess, base, codes, hexes):
    _req(sess, "POST", f"{base}/api/tiles/area", "POST /tiles/area",
         json={"hexes": random.sample(hexes, min(len(hexes), random.randint(20, 300)))})


def _p_detail(sess, base, codes, hexes):
    _req(sess, "GET", f"{base}/api/tiles/detail/{random.choice(hexes)}", "GET /tiles/detail")


def _p_excluded(sess, base, codes, hexes):
    _req(sess, "GET", f"{base}/api/tiles/excluded", "GET /tiles/excluded")


# (nombre, probe, necesita_codes, necesita_hexes)
_PROBES = [
    ("GET /tiles/warmup", _p_warmup, False, False),
    ("GET /tiles/variables", _p_variables, False, False),
    ("POST /tiles/variables-bulk", _p_bulk, True, False),
    ("POST /tiles/area", _p_area, False, True),
    ("GET /tiles/detail", _p_detail, False, True),
    ("GET /tiles/excluded", _p_excluded, False, False),  # lento a propósito: ver su techo
]


def _worker(probe, base, codes, hexes, stop: threading.Event) -> None:
    global _active, _max_active
    sess = requests.Session()
    sess.headers.update(_HEADERS)
    with _lock:
        _active += 1
        _max_active = max(_max_active, _active)
    try:
        while not stop.is_set():
            probe(sess, base, codes, hexes)
            time.sleep(random.uniform(0.02, 0.2))  # think time
    finally:
        with _lock:
            _active -= 1


def _poll_stats(base: str) -> dict:
    try:
        r = requests.get(f"{base}/api/_debug/stats", headers=_HEADERS, timeout=5)
        return r.json() if r.ok else {}
    except Exception:  # noqa: BLE001
        return {}


def _monitor(base: str, stop: threading.Event, interval: int = 2) -> None:
    last = 0
    while not stop.is_set():
        time.sleep(interval)
        with _lock:
            active, served = _active, _served
            cut = _now() - interval
            recent = [e for e in _events if e[0] >= cut and e[3]]
        rps = round((served - last) / interval, 1)
        last = served
        lat = sorted(e[2] for e in recent)
        p95 = lat[int(len(lat) * 0.95)] if lat else 0
        st = _poll_stats(base)
        db = _db_health()
        row = {"t": round(_now(), 1), "active": active, "rps": rps, "p95_ms": round(p95),
               "sys_used_mb": st.get("sys_used_mb", "-"), "sys_avail_mb": st.get("sys_avail_mb", "-"),
               "swap_used_mb": st.get("swap_used_mb", "-"), "inflight": st.get("inflight", "-"),
               "worker_rss_mb": st.get("worker_rss_mb", "-"),
               "db_active": db.get("db_active", "-"), "db_wait": db.get("db_wait", "-"),
               "db_conn": db.get("db_conn", "-"), "db_hit": db.get("db_hit", "-"),
               "db_temp_mb": db.get("db_temp_mb", "-")}
        with _lock:
            _timeline.append(row)
        print(f"    t={row['t']:>6}s activos={active:>4} rps={rps:>7} p95={row['p95_ms']:>6}ms "
              f"RAMus={row['sys_used_mb']}MB disp={row['sys_avail_mb']}MB "
              f"swap={row['swap_used_mb']} inflight={row['inflight']} rssWk={row['worker_rss_mb']} "
              f"| BDact={row['db_active']} BDwait={row['db_wait']} hit={row['db_hit']}% temp={row['db_temp_mb']}MB",
              flush=True)


def _pct(vals: list[float], p: float) -> float:
    if not vals:
        return 0.0
    v = sorted(vals)
    return v[min(len(v) - 1, int(len(v) * p))]


def _bootstrap(base: str) -> tuple[list[str], list[str]]:
    codes: list[str] = []
    hexes: list[str] = []
    try:
        r = requests.get(f"{base}/api/health", headers=_HEADERS, timeout=10)
        print(f"  preflight GET /api/health → {r.status_code}", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  !! preflight FALLÓ: {type(e).__name__}: {e} — revisa --base/auth", flush=True)
    try:
        r = requests.get(f"{base}/api/tiles/variables", headers=_HEADERS, timeout=30)
        print(f"  bootstrap GET /tiles/variables → {r.status_code}", flush=True)
        if r.ok:
            codes = [v["name"] for v in (r.json().get("variables") or []) if v.get("name")]
        elif r.status_code in (401, 403):
            print("  !! 401/403 → token/cookie inválido.", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  !! /tiles/variables FALLÓ: {type(e).__name__}", flush=True)
    if codes:  # hexes res8 reales (para area/detail) probando bbox chicos
        for _ in range(6):
            b = _rand_bbox()
            small = {"west": b["west"], "south": b["south"],
                     "east": b["west"] + 0.4, "north": b["south"] + 0.4}
            try:
                r = requests.post(f"{base}/api/tiles/variables-bulk", headers=_HEADERS, timeout=30,
                                  json={"codes": codes[:2], "bbox": small, "res": 8})
                if r.ok:
                    seen = {row["h3_index"] for rows in (r.json() or {}).values()
                            for row in rows or [] if row.get("h3_index")}
                    if seen:
                        hexes = random.sample(list(seen), min(len(seen), 3000))
                        print(f"  bootstrap hexes muestra (res8) → {len(hexes)}", flush=True)
                        break
            except Exception:  # noqa: BLE001
                pass
    return codes, hexes


# ── Diagnóstico BD (opcional) ────────────────────────────────────────────────────
# Mapa endpoint → la consulta SQL REAL que ejecuta (espejo del repositorio
# infrastructure/db/hexagon_query_repository.py). Para cada uno, EXPLAIN ANALYZE
# aísla el coste de la QUERY del coste de la APP (transferencia + Python).
_HEX_COLS = "h3_index, score_general, tipo_celda, lat, lng, variables"

def _ep_sql(name: str, codes: list[str], hexes: list[str]) -> tuple[str, dict] | None:
    """(sql, params) representativo del endpoint, o None si no aplica."""
    if "warmup" in name:  # el count(*) es el caro (scan completo)
        return "SELECT count(*) FROM hexagon", {}
    if "variables-bulk" in name or ("variable" in name and "variables" not in name):
        b = _rand_bbox()  # scan por bbox (btrees lat/lng sueltos) + JSONB por fila
        return (f"SELECT {_HEX_COLS} FROM hexagon "
                "WHERE lat >= %(s)s AND lat <= %(n)s "
                "AND lng >= %(w)s AND lng <= %(e)s",
                {"s": b["south"], "n": b["north"], "w": b["west"], "e": b["east"]})
    if name.endswith("/variables"):  # tabla meta diminuta (debería ser barato)
        return "SELECT variable, vmin, vmax FROM hexagon_meta", {}
    if "detail" in name and hexes:  # lookup por PK
        return (f"SELECT {_HEX_COLS} FROM hexagon WHERE h3_index = %(h)s",
                {"h": hexes[0]})
    if "area" in name and hexes:  # IN sobre PK
        return (f"SELECT {_HEX_COLS} FROM hexagon WHERE h3_index = ANY(%(hs)s)",
                {"hs": hexes[: min(len(hexes), 300)]})
    if "excluded" in name:  # scan completo SIN índice (tipo_celda / score_general)
        return ("SELECT h3_index FROM hexagon "
                "WHERE tipo_celda = 'excluida' OR score_general < 0", {})
    return None


def _db_connect(db_url: str):
    """(conn, driver) con psycopg(v3) o psycopg2, o (None, None) si falta/err."""
    try:
        import psycopg  # noqa: PLC0415  (v3)
        return psycopg.connect(db_url, connect_timeout=10), "psycopg"
    except ImportError:
        pass
    except Exception as e:  # noqa: BLE001
        print(f"  !! BD psycopg no conecta: {type(e).__name__}: {e}", flush=True)
        return None, None
    try:
        import psycopg2  # noqa: PLC0415
        return psycopg2.connect(db_url, connect_timeout=10), "psycopg2"
    except ImportError:
        print("  !! --db-url dado pero falta driver: pip install psycopg (o psycopg2)", flush=True)
    except Exception as e:  # noqa: BLE001
        print(f"  !! BD psycopg2 no conecta: {type(e).__name__}: {e}", flush=True)
    return None, None


def _db_explain(db_url: str, name: str, codes: list[str], hexes: list[str]) -> dict:
    """EXPLAIN (ANALYZE, BUFFERS) de la consulta del endpoint, SIN carga.
    Devuelve {ms, seq_scan, disk_blocks, plan} o {error}."""
    spec = _ep_sql(name, codes, hexes)
    if spec is None:
        return {"error": "sin SQL representativo"}
    sql, params = spec
    conn, _drv = _db_connect(db_url)
    if conn is None:
        return {"error": "sin conexión BD"}
    try:
        cur = conn.cursor()
        cur.execute("EXPLAIN (ANALYZE, BUFFERS, TIMING, FORMAT TEXT) " + sql, params)
        plan = "\n".join(str(r[0]) for r in cur.fetchall())
        cur.close()
        conn.rollback()
    except Exception as e:  # noqa: BLE001
        try:
            conn.close()
        except Exception:  # noqa: BLE001
            pass
        return {"error": f"{type(e).__name__}: {e}"}
    try:
        conn.close()
    except Exception:  # noqa: BLE001
        pass
    import re  # noqa: PLC0415
    m = re.search(r"Execution Time:\s*([\d.]+)\s*ms", plan)
    ms = float(m.group(1)) if m else None
    disk = sum(int(x) for x in re.findall(r"shared read=(\d+)", plan))
    return {"ms": ms, "seq_scan": "Seq Scan" in plan, "disk_blocks": disk, "plan": plan}


_DB_URL: str = ""
_db_mon_conn = None  # conexión persistente del monitor (salud BD en el timeline)


def _db_health() -> dict:
    """Snapshot ligero de salud de la BD (para el timeline). '-' si no hay conn."""
    global _db_mon_conn
    if not _DB_URL:
        return {}
    if _db_mon_conn is None:
        _db_mon_conn, _ = _db_connect(_DB_URL)
        if _db_mon_conn is None:
            return {}
    q = ("SELECT "
         "(SELECT count(*) FROM pg_stat_activity), "
         "(SELECT count(*) FROM pg_stat_activity WHERE state='active'), "
         "(SELECT count(*) FROM pg_stat_activity WHERE wait_event_type IS NOT NULL AND state='active'), "
         "(SELECT round(100.0*sum(blks_hit)/nullif(sum(blks_hit)+sum(blks_read),0),1) FROM pg_stat_database), "
         "(SELECT round(coalesce(sum(temp_bytes),0)/1048576.0,1) FROM pg_stat_database)")
    try:
        cur = _db_mon_conn.cursor()
        cur.execute(q)
        c, a, w, hit, temp = cur.fetchone()
        cur.close()
        _db_mon_conn.rollback()
        return {"db_conn": c, "db_active": a, "db_wait": w,
                "db_hit": hit if hit is not None else "-", "db_temp_mb": temp}
    except Exception:  # noqa: BLE001
        try:
            _db_mon_conn.close()
        except Exception:  # noqa: BLE001
            pass
        _db_mon_conn = None
        return {}


def _run_endpoint(name, probe, base, codes, hexes, stages, stage_secs,
                  break_err, break_p95) -> tuple[list[dict], int | None]:
    rows: list[dict] = []
    limit: int | None = None
    print(f"\n## {name}", flush=True)
    for users in stages:
        stop = threading.Event()
        with _lock:
            t_mark = _now()
        threads = [threading.Thread(target=_worker, args=(probe, base, codes, hexes, stop),
                                    daemon=True) for _ in range(users)]
        for t in threads:
            t.start()
            time.sleep(0.002)
        time.sleep(stage_secs)
        stop.set()
        for t in threads:
            t.join(timeout=5)
        with _lock:
            evs = [e for e in _events if e[0] >= t_mark and e[1] == name]
        lat = [e[2] for e in evs if e[3]]
        errp = round(100 * sum(1 for e in evs if not e[3]) / len(evs), 1) if evs else 0
        p50 = round(_pct(lat, 0.5))
        p95 = round(_pct(lat, 0.95))
        rps = round(len(evs) / stage_secs, 1)
        broke = errp >= break_err or p95 >= break_p95
        rows.append({"users": users, "req": len(evs), "err": errp, "p50": p50,
                     "p95": p95, "rps": rps, "broke": broke})
        print(f"  {users:>4} usuarios: req={len(evs):>6} err={errp:>5}% p50={p50:>6} "
              f"p95={p95:>6} rps={rps:>7}{'  ⚠ROMPIÓ' if broke else ''}", flush=True)
        if broke:
            limit = users
            break  # hallado el límite de ESTE endpoint → siguiente
    diag: dict = {}
    if _DB_URL:
        # EXPLAIN ANALYZE SIN carga: coste puro de la QUERY (aísla BD vs app).
        diag = _db_explain(_DB_URL, name, codes, hexes)
        if "error" in diag:
            print(f"  BD: {diag['error']}", flush=True)
        else:
            print(f"  BD query sola: {diag['ms']}ms  SeqScan={diag['seq_scan']}  "
                  f"disco={diag['disk_blocks']} bloques", flush=True)
    return rows, limit, diag


def _write_report(out_txt: str, cfg: list[str], per_ep: dict) -> None:
    base = out_txt.rsplit(".", 1)[0]
    with open(f"{base}-requests.csv", "w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["t_s", "endpoint", "ms", "ok", "status"])
        with _lock:
            w.writerows(sorted(_events, key=lambda e: e[0]))
    lines = list(cfg)
    for name, (rows, limit, _diag) in per_ep.items():
        lines.append(f"\n## {name}")
        lines.append(f"{'users':>6} {'req':>7} {'err%':>6} {'p50':>7} {'p95':>7} {'rps':>7}")
        for r in rows:
            lines.append(f"{r['users']:>6} {r['req']:>7} {r['err']:>6} {r['p50']:>7} "
                         f"{r['p95']:>7} {r['rps']:>7}{'  ⚠' if r['broke'] else ''}")
        lines.append(f"  → LÍMITE: {('ROMPIÓ en ' + str(limit)) if limit else 'estable hasta ' + str(rows[-1]['users']) if rows else 's/d'}")
    lines.append("\n## Resumen de LÍMITES por endpoint")
    for name, (rows, limit, _diag) in per_ep.items():
        lines.append(f"  {name:<28} → {('ROMPIÓ en ' + str(limit)) if limit else 'estable hasta ' + str(rows[-1]['users']) if rows else 's/d'}")

    # ── Veredicto BD vs APP (si se pasó --db-url) ──────────────────────────────
    has_db = any("plan" in d or "error" in d for _n, (_r, _l, d) in per_ep.items() if d)
    if has_db:
        lines.append("\n## Diagnóstico BD — ¿rompió la QUERY o la APP?")
        lines.append("  (EXPLAIN ANALYZE de la consulta real, SIN carga = coste puro de la BD)")
        for name, (rows, limit, d) in per_ep.items():
            if not d:
                continue
            if "error" in d:
                lines.append(f"  {name:<28} → BD n/d ({d['error']})")
                continue
            ms = d.get("ms")
            slow = (ms is not None and ms >= 1000) or d.get("seq_scan")
            # query lenta sola → cuello BD; query rápida pero endpoint rompió → cuello APP
            if slow:
                verd = "CUELLO = BD (la query sola ya es lenta)"
            elif limit:
                verd = "CUELLO = APP/concurrencia (la query sola es rápida)"
            else:
                verd = "ok"
            lines.append(f"  {name:<28} → query sola {ms}ms · SeqScan={d.get('seq_scan')} · "
                         f"disco={d.get('disk_blocks')}bloq → {verd}")
        lines.append("\n## Planes EXPLAIN (detalle)")
        for name, (rows, limit, d) in per_ep.items():
            if d and "plan" in d:
                lines.append(f"\n--- {name} ---")
                lines.append(d["plan"])
        lines.append("\n# swap del HOST de la BD: si la BD comparte server con la app, el 'swap'")
        lines.append("# del timeline (de /_debug/stats) YA lo incluye. Si es server aparte, usa")
        lines.append("# hit% (cache) y temp(MB) del timeline como proxy de presión de RAM en la BD.")

    lines.append("\n## Timeline RAM+BD (cada 2s; '-' si falta DEBUG_INFLIGHT=1 / --db-url)")
    lines.append(f"{'t(s)':>6} {'activos':>7} {'rps':>7} {'p95ms':>6} {'RAMus':>6} "
                 f"{'disp':>6} {'swap':>5} {'inflight':>8} {'rssWk':>6} "
                 f"{'BDact':>6} {'BDwait':>6} {'BDconn':>6} {'hit%':>5} {'tempMB':>6}")
    for r in _timeline:
        lines.append(f"{r['t']:>6} {r['active']:>7} {r['rps']:>7} {r['p95_ms']:>6} "
                     f"{r['sys_used_mb']:>6} {r['sys_avail_mb']:>6} {r['swap_used_mb']:>5} "
                     f"{r['inflight']:>8} {r['worker_rss_mb']:>6} "
                     f"{r.get('db_active','-'):>6} {r.get('db_wait','-'):>6} "
                     f"{r.get('db_conn','-'):>6} {r.get('db_hit','-'):>5} {r.get('db_temp_mb','-'):>6}")
    with open(out_txt, "w", encoding="utf-8") as f:
        f.write("\n".join(lines) + "\n")
    print(f"\nReportes: {out_txt} · {base}-requests.csv", flush=True)


_HEADERS: dict = {}


def main() -> None:
    global _t0, _TIMEOUT, _HEADERS, _DB_URL
    ap = argparse.ArgumentParser()
    ap.add_argument("--base", required=True)
    ap.add_argument("--token")
    ap.add_argument("--cookie")
    ap.add_argument("--db-url", default="",
                    help="DSN Postgres (opcional) para EXPLAIN+salud BD. "
                         "Ej: postgresql://USUARIO:CLAVE@HOST_BD:5432/NOMBRE_BD")
    ap.add_argument("--stages", default="10,25,50,100,200,400,700,1000")
    ap.add_argument("--stage-secs", type=int, default=15)
    ap.add_argument("--break-err", type=float, default=25.0)
    ap.add_argument("--break-p95", type=float, default=8000.0)
    ap.add_argument("--req-timeout", type=float, default=10.0)
    ap.add_argument("--only", default="", help="probar SOLO estos endpoints (subcadena, coma)")
    ap.add_argument("--skip", default="excluded", help="saltar estos endpoints (subcadena, coma)")
    ap.add_argument("--out", default="loadtest-report.txt")
    args = ap.parse_args()

    _TIMEOUT = args.req_timeout
    _DB_URL = args.db_url or ""
    if args.token:
        _HEADERS["Authorization"] = f"Bearer {args.token}"
    if args.cookie:
        _HEADERS["Cookie"] = args.cookie

    _t0 = time.time()
    codes, hexes = _bootstrap(args.base)
    stages = [int(x) for x in args.stages.split(",") if x.strip()]
    only = [s.strip() for s in args.only.split(",") if s.strip()]
    skip = [s.strip() for s in args.skip.split(",") if s.strip()]
    cfg = [
        f"# Prueba de carga UNITARIA por endpoint — {args.base}",
        f"# auth: {'token' if args.token else 'cookie' if args.cookie else 'NINGUNO'} · "
        f"codes={len(codes)} hexes-muestra={len(hexes)}",
        f"# rampa: {stages} · {args.stage_secs}s/etapa · corte err>{args.break_err}% o p95>{args.break_p95:.0f}ms",
        f"# diagnóstico BD: {'ON (EXPLAIN + salud BD)' if _DB_URL else 'OFF (pasa --db-url para activarlo)'}",
    ]
    for c in cfg:
        print(c, flush=True)

    mon_stop = threading.Event()
    threading.Thread(target=_monitor, args=(args.base, mon_stop), daemon=True).start()

    per_ep: dict = {}
    for name, probe, need_codes, need_hexes in _PROBES:
        if only and not any(o in name for o in only):
            continue
        if any(s in name for s in skip):
            continue
        if need_codes and not codes:
            print(f"\n## {name} — SALTADO (no hay codes)", flush=True)
            continue
        if need_hexes and not hexes:
            print(f"\n## {name} — SALTADO (no hay hexes de muestra)", flush=True)
            continue
        per_ep[name] = _run_endpoint(name, probe, args.base, codes, hexes, stages,
                                     args.stage_secs, args.break_err, args.break_p95)

    mon_stop.set()
    time.sleep(0.2)
    if _db_mon_conn is not None:
        try:
            _db_mon_conn.close()
        except Exception:  # noqa: BLE001
            pass
    _write_report(args.out, cfg, per_ep)


if __name__ == "__main__":
    main()
