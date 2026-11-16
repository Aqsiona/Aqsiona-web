#!/usr/bin/env python3
"""Actualiza los indicadores embebidos en index.html (bloques JSON
"tracking-data" = Argentina y "global-data" = internacional).

Lo ejecuta GitHub Actions cada hora. Si una fuente falla o devuelve un valor
sospechoso, se conserva el dato anterior de esa variable. Nunca inventa datos.
"""
import datetime as dt
import json
import math
import re
import ssl
import sys
import urllib.parse
import urllib.request

INDEX = sys.argv[1] if len(sys.argv) > 1 else "index.html"
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
TODAY = dt.datetime.now(dt.timezone.utc).date()
D40 = TODAY - dt.timedelta(days=45)


def get(url, insecure_ok=False):
    req = urllib.request.Request(url, headers={"User-Agent": UA, "Accept": "*/*"})
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return r.read().decode("utf-8", "replace")
    except ssl.SSLError:
        raise
    except Exception as e:
        # El BCRA suele publicar una cadena de certificados incompleta.
        if insecure_ok and "CERTIFICATE" in str(e).upper():
            ctx = ssl._create_unverified_context()
            with urllib.request.urlopen(req, timeout=40, context=ctx) as r:
                return r.read().decode("utf-8", "replace")
        raise


def get_json(url, **kw):
    return json.loads(get(url, **kw))


def clean(pairs, nd):
    """pairs: iterable (fecha ISO, valor) -> lista ascendente, sin duplicados."""
    d = {}
    for f, v in pairs:
        if v is None:
            continue
        v = float(v)
        if not math.isfinite(v):
            continue
        d[str(f)[:10]] = round(v, nd) if nd else int(round(v))
    return sorted(d.items())


def window(rows):
    """Desde la última observación <= (última - 30 días) hasta la última."""
    last = dt.date.fromisoformat(rows[-1][0])
    cut = (last - dt.timedelta(days=30)).isoformat()
    start = 0
    for i, (f, _) in enumerate(rows):
        if f <= cut:
            start = i
    return rows[start:]


def item(rows):
    return {"asOf": rows[-1][0], "value": rows[-1][1],
            "dates": [f for f, _ in rows], "series": [v for _, v in rows]}


# ---------------------------------------------------------------- Argentina
def bcra(var_id, nd):
    url = ("https://api.bcra.gob.ar/estadisticas/v4.0/monetarias/%d?desde=%s&hasta=%s&limit=100"
           % (var_id, D40.isoformat(), TODAY.isoformat()))
    res = get_json(url, insecure_ok=True)["results"]
    rows = []
    for r in res if isinstance(res, list) else [res]:
        for x in r.get("detalle", [r]):
            rows.append((x["fecha"], x["valor"]))
    return item(window(clean(rows, nd)))


def riesgo():
    rows = []
    try:
        url = ("https://mercados.ambito.com//riesgopais/historico-general/%s/%s"
               % (D40.isoformat(), TODAY.isoformat()))
        for r in get_json(url):
            m = re.match(r"(\d{2})-(\d{2})-(\d{4})$", str(r[0]).strip())
            if m:
                v = float(str(r[1]).replace(".", "").replace(",", "."))
                rows.append(("%s-%s-%s" % (m.group(3), m.group(2), m.group(1)), v))
    except Exception as e:
        print("  riesgo: Ámbito falló (%s); uso ArgentinaDatos" % e)
    if len(rows) < 15:
        data = get_json("https://api.argentinadatos.com/v1/finanzas/indices/riesgo-pais")
        rows = [(x["fecha"], x["valor"]) for x in data if x["fecha"] >= D40.isoformat()]
    return item(window(clean(rows, 0)))


def inflacion():
    data = get_json("https://api.argentinadatos.com/v1/finanzas/indices/inflacion")
    rows = clean(((x["fecha"][:7] + "-01", x["valor"]) for x in data), 1)[-12:]
    return item([(f[:7], v) for f, v in rows])


def actividad():
    url = ("https://apis.datos.gob.ar/series/api/series?ids=143.3_NO_PR_2004_A_21"
           "&limit=32&sort=desc&format=json")
    idx = {r[0][:7]: float(r[1]) for r in get_json(url)["data"] if r[1] is not None}
    out = []
    for ym in sorted(idx):
        prev = "%04d-%s" % (int(ym[:4]) - 1, ym[5:])
        if prev in idx:
            out.append((ym, round((idx[ym] / idx[prev] - 1) * 100, 2)))
    return item(out[-12:])


# ------------------------------------------------------------ Internacional
def yahoo(symbol, nd):
    def f():
        last = None
        for host in ("query1", "query2"):
            try:
                url = ("https://%s.finance.yahoo.com/v8/finance/chart/%s?range=3mo&interval=1d"
                       % (host, urllib.parse.quote(symbol)))
                r = get_json(url)["chart"]["result"][0]
                off = r["meta"].get("gmtoffset", 0)
                rows = []
                for t, c in zip(r["timestamp"], r["indicators"]["quote"][0]["close"]):
                    d = (dt.datetime.fromtimestamp(t, dt.timezone.utc)
                         + dt.timedelta(seconds=off)).date()
                    if c is not None and d.weekday() < 5:
                        rows.append((d.isoformat(), c))
                return item(window(clean(rows, nd)))
            except Exception as e:
                last = e
        raise last
    return f


def ust10():
    rows = []
    first = TODAY.replace(day=1)
    for m in ((first - dt.timedelta(days=1)), TODAY):
        url = ("https://home.treasury.gov/resource-center/data-chart-center/interest-rates/"
               "pages/xml?data=daily_treasury_yield_curve&field_tdr_date_value_month=%s"
               % m.strftime("%Y%m"))
        try:
            xml = get(url)
        except Exception as e:
            print("  ust10: mes %s falló (%s)" % (m.strftime("%Y%m"), e))
            continue
        for entry in re.findall(r"<entry>.*?</entry>", xml, re.S):
            d = re.search(r"<d:NEW_DATE[^>]*>(\d{4}-\d{2}-\d{2})", entry)
            v = re.search(r"<d:BC_10YEAR[^>]*>([\d.]+)<", entry)
            if d and v:
                rows.append((d.group(1), v.group(1)))
    return item(window(clean(rows, 2)))


def fed():
    refs = get_json("https://markets.newyorkfed.org/api/rates/unsecured/effr/last/40.json")["refRates"]
    it = item(window(clean(((x["effectiveDate"], x["percentRate"]) for x in refs), 2)))
    last = max(refs, key=lambda x: x["effectiveDate"])
    fmt = lambda n: ("%.2f" % float(n)).replace(".", ",")
    it["range"] = "%s–%s%%" % (fmt(last["targetRateFrom"]), fmt(last["targetRateTo"]))
    return it


TRACKING = {  # id: (función, cambio máximo admitido en veces, mensual)
    "inflacion": (inflacion, None, True),
    "dolar": (lambda: bcra(5, 2), 2, False),
    "badlar": (lambda: bcra(7, 2), 2, False),
    "actividad": (actividad, None, True),
    "riesgo": (riesgo, 3, False),
}
GLOBAL = {
    "sp500": (yahoo("^GSPC", 2), 2, False),
    "dxy": (yahoo("DX-Y.NYB", 2), 2, False),
    "brent": (yahoo("BZ=F", 2), 2, False),
    "oro": (yahoo("GC=F", 2), 2, False),
    "soja": (yahoo("ZS=F", 2), 2, False),
    "eurusd": (yahoo("EURUSD=X", 4), 2, False),
    "usdbrl": (yahoo("BRL=X", 4), 2, False),
    "vix": (yahoo("^VIX", 2), 3, False),
    "ust10": (ust10, 2, False),
    "fed": (fed, 2, False),
}


def valid(new, old, tol, monthly):
    n = len(new["series"])
    if n != len(new["dates"]) or new["series"][-1] != new["value"]:
        return "serie inconsistente"
    if monthly and n != 12:
        return "se esperaban 12 meses y llegaron %d" % n
    if not monthly and n < 15:
        return "solo %d observaciones" % n
    if new["asOf"] < old["asOf"]:
        return "dato más viejo que el publicado"
    # Control de cordura contra errores de unidad o de lectura de la fuente.
    if tol and old["value"] and new["value"]:
        ratio = abs(new["value"] / old["value"])
        if max(ratio, 1 / ratio) > tol:
            return "salto sospechoso %s -> %s" % (old["value"], new["value"])
    return None


def refresh(block, spec, name):
    changed, failed = [], []
    for key, (fn, tol, monthly) in spec.items():
        old = block["items"].get(key)
        if old is None:
            continue
        try:
            new = fn()
            err = valid(new, old, tol, monthly)
            if err:
                raise ValueError(err)
        except Exception as e:
            failed.append(key)
            print("[%s] %s: FALLÓ (%s) - se conserva el dato anterior" % (name, key, e))
            continue
        merged = dict(old)
        merged.update(new)
        if merged != old:
            changed.append(key)
            print("[%s] %s: %s (%s) -> %s (%s)" % (name, key, old["value"], old["asOf"],
                                                  new["value"], new["asOf"]))
            block["items"][key] = merged
        else:
            print("[%s] %s: sin cambios" % (name, key))
    return changed, failed


def main():
    html = open(INDEX, encoding="utf-8").read()
    now = dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    total_changed, total_failed = [], []
    for bid, spec, dump in (
        ("tracking-data", TRACKING, lambda d: json.dumps(d, ensure_ascii=False, indent=1)),
        ("global-data", GLOBAL, lambda d: json.dumps(d, ensure_ascii=False, separators=(",", ":"))),
    ):
        pat = re.compile(r'(<script type="application/json" id="%s">)(.*?)(</script>)' % bid, re.S)
        m = pat.search(html)
        if not m:
            print("No se encontró el bloque %s" % bid)
            sys.exit(1)
        block = json.loads(m.group(2))
        changed, failed = refresh(block, spec, bid)
        total_failed += failed
        if changed:
            block["updatedAt"] = now
            html = html[:m.start(2)] + dump(block) + html[m.end(2):]
            total_changed += changed
    if total_changed:
        open(INDEX, "w", encoding="utf-8", newline="").write(html)
    print("\nCambiaron: %s" % (", ".join(total_changed) or "nada"))
    print("Fallaron: %s" % (", ".join(total_failed) or "ninguna"))
    n = len(TRACKING) + len(GLOBAL)
    if len(total_failed) == n:
        sys.exit(1)  # todas las fuentes fallaron: marcar la corrida como fallida


if __name__ == "__main__":
    main()
