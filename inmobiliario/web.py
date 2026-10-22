"""Web local: listado con filtros, mapa y marcas, sobre la base SQLite.

    python -m inmobiliario web [--puerto 8765] [--abrir]

Solo biblioteca estandar. Escucha UNICAMENTE en 127.0.0.1: la ve quien esta
sentado frente a esta compu y nadie mas (ni la red de la casa). No hay login
porque no hace falta; las defensas que si hay son las que evitan que OTRA
pagina abierta en el mismo navegador marque propiedades a escondidas:

  * el encabezado Host tiene que ser 127.0.0.1 o localhost con nuestro puerto
    (frena el "DNS rebinding");
  * todo POST exige un token CSRF que se genera al arrancar el proceso, y un
    encabezado Origin que sea el de esta misma web.

Lee la tabla `ranking`, que deja `informe` (ver ranking.py), y escribe las
marcas en `marcas` y `caidas`: las mismas tablas que usa el bot de Telegram,
asi que los dos ven lo mismo.
"""
from __future__ import annotations

import hmac
import html
import json
import re
import secrets
import sqlite3
import threading
import unicodedata
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import parse_qs, quote, urlencode, urlsplit

from .db import Base, USUARIO

ESTATICOS = Path(__file__).resolve().parent / "web_estatico"
TIPOS_ESTATICOS = {"estilo.css": "text/css", "listado.js": "text/javascript",
                   "mapa.js": "text/javascript"}

POR_PAGINA = 40
TOPE_MAPA = 3000
MAX_CUERPO = 20_000          # un POST de marca pesa unos cientos de bytes

# Lista blanca: lo unico que se interpola en el ORDER BY sale de aca.
ORDENES = {
    "puntaje": ("p.puntaje DESC", "Puntaje"),
    "rendimiento": ("p.rendimiento DESC", "Rendimiento"),
    "precio": ("p.precio_usd ASC", "Precio: menor primero"),
    "precio_m2": ("p.precio_m2 ASC", "USD/m²: menor primero"),
    "recientes": ("p.vista_primera DESC", "Vistas más recientemente"),
    # La cola de pendientes se lee como cola: lo ultimo que aviso el bot primero.
    "aviso": ("n.fecha DESC, p.puntaje DESC", "Avisada más recientemente"),
}
ESTADOS = ("sin_marcar", "por_revisar", "interesa", "no_interesa", "todas")

CSP = ("default-src 'self'; script-src 'self' https://cdnjs.cloudflare.com; "
       "style-src 'self' 'unsafe-inline' https://cdnjs.cloudflare.com; "
       "img-src 'self' data: blob: https:; connect-src 'self'; object-src 'none'; "
       "frame-ancestors 'none'; base-uri 'self'; form-action 'self'")


# ------------------------------------------------------------------ formato
def e(valor) -> str:
    return html.escape("" if valor is None else str(valor), quote=True)


def nro(v, dec=0) -> str:
    """Miles con punto y decimales con coma, como se escriben los precios aca."""
    if v is None:
        return "—"
    return f"{float(v):,.{dec}f}".replace(",", "§").replace(".", ",").replace("§", ".")


def usd(v) -> str:
    return "—" if v is None else "USD " + nro(v)


def pct(v) -> str:
    return "—" if v is None else nro(float(v) * 100, 1) + "%"


def color_puntaje(p) -> str:
    return "alto" if p >= 80 else "medio" if p >= 65 else "bajo"


def url_segura(u) -> str:
    """Solo http(s): el texto viene de portales y no se confia en un javascript:."""
    return u if isinstance(u, str) and re.match(r"https?://", u) else ""


def sin_acentos(texto) -> str:
    n = unicodedata.normalize("NFD", str(texto or ""))
    return "".join(c for c in n if unicodedata.category(c) != "Mn").lower()


def fecha_corta(iso, formato="%d/%m %H:%M") -> str:
    from datetime import datetime
    try:
        return datetime.fromisoformat(iso).strftime(formato)
    except (TypeError, ValueError):
        return ""


# ------------------------------------------------------------------ filtros
def _num(valor, tipo=float):
    try:
        return tipo(valor)
    except (TypeError, ValueError):
        return tipo(0)


def filtros(q: dict) -> dict:
    g = lambda k, d="": (q.get(k) or [d])[0].strip()
    estado = g("estado", "sin_marcar")
    orden = g("orden")
    return {
        "estado": estado if estado in ESTADOS else "sin_marcar",
        # Una sola propiedad, para venir desde el mapa. Cuando esta puesta manda
        # ella sola: el filtro de estado no puede esconder justo la que se pidio.
        "clave": g("clave"),
        # Todos los avisos de un mismo edificio (o cuadra).
        "edificio": g("edificio"),
        "barrio": g("barrio"),
        "tipo": g("tipo") if g("tipo") in ("depto", "ph", "casa") else "",
        "q": g("q"),
        "precio_max": _num(g("precio_max")),
        "precio_min": _num(g("precio_min")),
        "amb": _num(g("amb"), int),
        "rend_min": _num(g("rend_min")),
        "puntaje_min": _num(g("puntaje_min")),
        "solo_bajadas": g("bajadas") == "1",
        # "Por revisar" ordena por lo ultimo avisado, salvo que se pida otra cosa.
        "orden": orden if orden in ORDENES else ("aviso" if estado == "por_revisar"
                                                  else "puntaje"),
        "pagina": max(1, _num(g("pagina", "1"), int)),
    }


def donde(f: dict):
    """(condicion SQL, parametros). Todo lo del navegador va como parametro."""
    if f["clave"]:
        return "p.clave = ?", [f["clave"]]
    if f["edificio"]:
        return "p.edificio = ?", [f["edificio"]]
    w, p = ["1=1"], []
    if f["estado"] == "sin_marcar":
        w.append("m.estado IS NULL")
    elif f["estado"] == "por_revisar":
        # Lo que el bot aviso y todavia no se miro: llegan 12 avisos al celular,
        # se miran 5 y los otros 7 se pierden en el chat.
        w.append("m.estado IS NULL AND n.clave IS NOT NULL")
    elif f["estado"] in ("interesa", "no_interesa"):
        w.append("m.estado = ?")
        p.append(f["estado"])
    for campo, col in (("barrio", "p.barrio"), ("tipo", "p.tipo")):
        if f[campo]:
            w.append(f"{col} = ?")
            p.append(f[campo])
    if f["amb"] > 0:
        w.append("p.ambientes = ?")
        p.append(f["amb"])
    if f["precio_min"] > 0:
        w.append("p.precio_usd >= ?")
        p.append(f["precio_min"])
    if f["precio_max"] > 0:
        w.append("p.precio_usd <= ?")
        p.append(f["precio_max"])
    if f["rend_min"] > 0:
        w.append("p.rendimiento >= ?")
        p.append(f["rend_min"] / 100)
    if f["puntaje_min"] > 0:
        w.append("p.puntaje >= ?")
        p.append(f["puntaje_min"])
    if f["solo_bajadas"]:
        w.append("p.bajo_precio = 1")
    if f["q"]:
        w.append("(sinacentos(p.direccion) LIKE ? OR sinacentos(p.titulo) LIKE ? "
                 "OR sinacentos(p.barrio) LIKE ?)")
        like = f"%{sin_acentos(f['q'])}%"
        p += [like, like, like]
    return " AND ".join(w), p


DESDE = ("FROM ranking p LEFT JOIN marcas m ON m.clave = p.clave AND m.usuario = ? "
         "LEFT JOIN notificadas n ON n.clave = p.clave")


def abrir(ruta: Path) -> Base:
    base = Base(ruta, USUARIO)
    base.con.create_function("sinacentos", 1, sin_acentos, deterministic=True)
    return base


def listado(con, f):
    w, par = donde(f)
    total = con.execute(f"SELECT COUNT(*) {DESDE} WHERE {w}", [USUARIO] + par).fetchone()[0]
    desde = POR_PAGINA * (f["pagina"] - 1)
    filas = con.execute(
        f"SELECT p.*, m.estado AS marca, m.nota AS mi_nota, n.fecha AS avisada "
        f"{DESDE} WHERE {w} ORDER BY {ORDENES[f['orden']][0]} "
        f"LIMIT {POR_PAGINA} OFFSET {desde}", [USUARIO] + par).fetchall()
    return filas, total


def puntos_mapa(con, f, tope=TOPE_MAPA):
    w, par = donde(f)
    return [dict(r) for r in con.execute(
        f"SELECT p.clave, p.lat, p.lon, p.puntaje, p.precio_usd, p.rendimiento, "
        f"p.alquiler_estimado, p.barrio, p.direccion, p.ambientes, p.m2, p.url, "
        f"m.estado AS marca {DESDE} WHERE {w} AND p.lat IS NOT NULL "
        f"ORDER BY p.puntaje DESC LIMIT {int(tope)}", [USUARIO] + par)]


def resumen(con):
    q = lambda sql, *a: con.execute(sql, a).fetchone()[0]
    return {
        "total": q("SELECT COUNT(*) FROM ranking"),
        "bajadas": q("SELECT COALESCE(SUM(bajo_precio),0) FROM ranking"),
        "interesan": q("SELECT COUNT(*) FROM marcas WHERE estado='interesa' AND usuario=?",
                       USUARIO),
        "por_revisar": q(
            "SELECT COUNT(*) FROM notificadas n JOIN ranking r ON r.clave = n.clave "
            "WHERE NOT EXISTS (SELECT 1 FROM marcas x WHERE x.clave = n.clave "
            "AND x.usuario = ?)", USUARIO),
        "ultima": q("SELECT MAX(actualizada) FROM ranking"),
    }


def barrios(con):
    return con.execute(
        "SELECT barrio, COUNT(*) n FROM ranking WHERE barrio IS NOT NULL AND barrio <> '' "
        "GROUP BY barrio ORDER BY barrio").fetchall()


# ------------------------------------------------------------------- marcas
def aplicar_marca(base: Base, d: dict):
    """Guarda una marca pedida desde el navegador. Devuelve la respuesta JSON.

    Usa los mismos metodos de Base que el bot de Telegram, asi las dos
    superficies escriben exactamente lo mismo. La firma geografica (direccion,
    coordenadas, precio, ambientes) viaja con la marca para que siga a la
    propiedad si reaparece publicada por otra inmobiliaria o en otro portal.
    """
    clave = str(d.get("clave") or "")
    estado = d.get("estado") or ""
    if not clave:
        raise ValueError("Falta la propiedad")
    if estado not in ("interesa", "no_interesa", "caida", ""):
        raise ValueError("Estado inválido")
    con = base.con

    if estado == "":
        base.desmarcar(clave, USUARIO)
        return {"ok": True, "estado": None}

    fila = con.execute("SELECT * FROM ranking WHERE clave=?", (clave,)).fetchone() \
        or con.execute("SELECT * FROM publicaciones WHERE clave=?", (clave,)).fetchone()
    if fila is None:
        raise ValueError("No existe esa propiedad")

    # Una caida no es una opinion: el aviso ya no existe, vale para todos y por
    # eso no lleva usuario. Sale del listado en el acto; la fila de `caidas` es
    # la que impide que la recoleccion la vuelva a subir.
    if estado == "caida":
        base.marcar_caida(clave, quien="web")
        con.execute("DELETE FROM ranking WHERE clave=?", (clave,))
        con.commit()
        return {"ok": True, "estado": "caida"}

    # Si no viene la clave "nota", la que ya hay se conserva (cambiar de opinion
    # no borra el motivo); si viene vacia, se borra.
    kwargs = {}
    if "nota" in d:
        nota = str(d.get("nota") or "").strip()[:500]
        kwargs["nota"] = nota or None
    edificio = bool(d.get("edificio")) and estado == "no_interesa"
    base.marcar(SimpleNamespace(**{k: fila[k] for k in
                ("clave", "direccion", "lat", "lon", "precio_usd", "ambientes")}),
                estado, quien="web", origen="web", edificio=edificio, **kwargs)

    # "Todo el edificio, no": los demas avisos del mismo edificio se marcan
    # igual. Si el grupo es la cuadra (se agrupo por direccion y no por
    # coordenadas) solo se toman los de la misma direccion.
    if edificio and "edificio" in fila.keys():
        for h in con.execute(
                "SELECT clave, direccion, lat, lon, precio_usd, ambientes FROM ranking "
                "WHERE edificio=? AND clave<>? AND (edificio_exacto=1 "
                "OR lower(direccion)=lower(?)) AND clave NOT IN "
                "(SELECT clave FROM marcas WHERE usuario=?)",
                (fila["edificio"], clave, fila["direccion"] or "", USUARIO)).fetchall():
            base.marcar(SimpleNamespace(**dict(h)), "no_interesa", quien="web",
                        origen="web", edificio=True, nota=None)
    return {"ok": True, "estado": estado}


# --------------------------------------------------------------------- HTML
def cabecera(titulo, activa, res, csrf, extra_head="") -> str:
    kpis = [
        f'<span><a href="/?estado=todas"><b>{nro(res["total"])}</b> publicadas</a></span>',
        f'<span><a href="/?estado=todas&amp;bajadas=1"><b>{nro(res["bajadas"])}</b> '
        f'bajaron de precio</a></span>',
        f'<span><a href="/?estado=interesa" title="Las que marcaste «me interesa»">'
        f'★ <b>{nro(res["interesan"])}</b></a></span>',
    ]
    # Solo si queda algo: un cero permanente en la barra es ruido.
    if res["por_revisar"] > 0:
        kpis.append(f'<span><a href="/?estado=por_revisar" title="Avisadas por Telegram '
                    f'que todavía no miraste">📩 <b>{nro(res["por_revisar"])}</b> '
                    f'por revisar</a></span>')
    if res["ultima"]:
        kpis.append(f'<span>datos del <b>{e(fecha_corta(res["ultima"]))}</b></span>')
    return f"""<!doctype html>
<html lang="es"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<meta name="csrf" content="{e(csrf)}">
<title>{e(titulo)} · Buscador inmobiliario</title>
<link rel="stylesheet" href="/static/estilo.css">{extra_head}
</head><body>
<div class="barra"><div class="fila">
  <span class="marca-sitio">Oportunidades</span>
  <nav class="tabs">
    <a href="/" class="{'on' if activa == 'lista' else ''}">Listado</a>
    <a href="/mapa" class="{'on' if activa == 'mapa' else ''}">Mapa</a>
  </nav>
  <div class="kpis">{''.join(kpis)}</div>
</div></div>
<main>
"""


PIE = "</main></body></html>"


def pagina_vacia(csrf) -> str:
    res = {"total": 0, "bajadas": 0, "interesan": 0, "por_revisar": 0, "ultima": None}
    return (cabecera("Sin datos", "lista", res, csrf) +
            '<div class="vacio"><b>Todavía no hay propiedades para mostrar.</b><br>'
            'Corré <code>python -m inmobiliario recolectar</code> y después '
            '<code>python -m inmobiliario informe</code>, y recargá esta página.</div>'
            + PIE)


def url_con(q: dict, **cambios) -> str:
    base = {k: v[0] for k, v in q.items() if v and v[0] != ""}
    base.update({k: str(v) for k, v in cambios.items()})
    base = {k: v for k, v in base.items() if v != ""}
    return "/?" + urlencode(base)


def form_listado(f, bs) -> str:
    def opts(pares, actual):
        return "".join(f'<option value="{e(k)}"{" selected" if str(actual) == str(k) else ""}>'
                       f'{e(v)}</option>' for k, v in pares)
    estados = [("sin_marcar", "Sin marcar"), ("por_revisar", "📩 Por revisar (Telegram)"),
               ("interesa", "★ Me interesan"), ("no_interesa", "Descartadas"),
               ("todas", "Todas")]
    barrios_ = [("", "Todos")] + [(b["barrio"], f'{b["barrio"]} ({b["n"]})') for b in bs]
    tipos = [("", "Todos"), ("depto", "Departamento"), ("ph", "PH"), ("casa", "Casa")]
    ambs = [("0", "Todos")] + [(str(i), str(i)) for i in range(1, 6)]
    ords = [(k, v[1]) for k, v in ORDENES.items()]
    val = lambda x: e(x) if x else ""
    return f"""<form class="filtros" method="get" action="/">
  <div class="campo"><label for="estado">Estado</label>
    <select name="estado" id="estado">{opts(estados, f["estado"])}</select></div>
  <div class="campo"><label for="barrio">Barrio</label>
    <select name="barrio" id="barrio">{opts(barrios_, f["barrio"])}</select></div>
  <div class="campo"><label for="tipo">Tipo</label>
    <select name="tipo" id="tipo">{opts(tipos, f["tipo"])}</select></div>
  <div class="campo"><label for="amb">Ambientes</label>
    <select name="amb" id="amb">{opts(ambs, f["amb"])}</select></div>
  <div class="campo"><label for="precio_max">Precio hasta</label>
    <input type="number" name="precio_max" id="precio_max" step="5000"
           value="{val(f["precio_max"] or "")}" placeholder="USD"></div>
  <div class="campo"><label for="rend_min">Rinde desde</label>
    <input type="number" name="rend_min" id="rend_min" step="0.5"
           value="{val(f["rend_min"] or "")}" placeholder="%"></div>
  <div class="campo"><label for="q">Buscar</label>
    <input type="search" name="q" id="q" value="{e(f["q"])}" placeholder="calle o texto"></div>
  <div class="campo"><label for="orden">Ordenar por</label>
    <select name="orden" id="orden">{opts(ords, f["orden"])}</select></div>
  <label class="chk"><input type="checkbox" name="bajadas" value="1"
    {"checked" if f["solo_bajadas"] else ""}> Sólo las que bajaron</label>
  <button type="submit">Filtrar</button>
  <a href="/"><button type="button" class="sec">Limpiar</button></a>
</form>"""


def ficha(p) -> str:
    motivos = json.loads(p["motivos"] or "[]")
    pois = json.loads(p["pois_cerca"] or "[]")
    destacados = [m for m in motivos if "vs mediana" in m
                  or m.startswith(("BAJO DE PRECIO", "REVISAR", "expensas altas",
                                   "expensas bajas")) or "vendedor cansado" in m][:3]
    url = url_segura(p["url"])
    foto = url_segura(p["foto"])
    chips = []
    if p["rendimiento"]:
        alq = f' · {usd(p["alquiler_estimado"])}/mes' if p["alquiler_estimado"] else ""
        chips.append(f'<span class="chip b">rinde {pct(p["rendimiento"])}{e(alq)}</span>')
    if p["avisada"]:
        chips.append(f'<span class="chip">📩 avisada {e(fecha_corta(p["avisada"] + "T00:00", "%d/%m"))}</span>')
    if p["bajo_precio"]:
        chips.append(f'<span class="chip o">bajó de {usd(p["precio_anterior"])}</span>')
    if (p["copias"] or 1) > 1:
        chips.append(f'<span class="chip">en {int(p["copias"])} portales</span>')
    total, vis = int(p["en_edificio_total"] or 1), int(p["en_edificio"] or 1)
    if total > 1:
        donde_ = "en el edificio" if p["edificio_exacto"] else "en la cuadra"
        extra = f", {vis} con puntaje" if vis < total else ""
        chips.append(
            f'<a class="chip edificio" href="/?edificio={quote(str(p["edificio"]))}" '
            f'title="{total} en venta {donde_}">{"🏢" if p["edificio_exacto"] else "📍"} '
            f'{total} en venta {donde_}{extra}</a>')
    if p["geo_dudosa"]:
        chips.append('<span class="chip o">verificar ubicación</span>')
    if p["emprendimiento"]:
        chips.append('<span class="chip">emprendimiento</span>')
    if p["marca"]:
        chips.append(f'<span class="chip {"b" if p["marca"] == "interesa" else ""}">'
                     f'{"★ interesa" if p["marca"] == "interesa" else "descartada"}</span>')
    lis = "".join(f"<li>{e(m)}</li>" for m in destacados)
    if pois:
        cerca = ", ".join(f"{n} ({d} m)" for n, d in pois[:2])
        lis += f"<li>cerca de {e(cerca)}</li>"
    meta = f'<b>{e(p["barrio"])}</b> · {e(p["tipo"])}'
    if p["ambientes"]:
        meta += f' · {int(p["ambientes"])} amb'
    if p["m2"]:
        meta += f' · {nro(p["m2"])} m²'
    if p["antiguedad"] is not None:
        meta += f' · {int(p["antiguedad"])} años'
    titulo = e(p["direccion"] or (p["titulo"] or "")[:70])
    titulo_html = f'<a href="{e(url)}" target="_blank" rel="noopener noreferrer">{titulo}</a>' \
        if url else titulo
    tapa = (f'<a class="tapa" href="{e(url)}" target="_blank" rel="noopener noreferrer">'
            f'<img class="foto" src="{e(foto)}" alt="" loading="lazy" '
            f'referrerpolicy="no-referrer"></a>') if foto else ""
    nota = f'<p class="nota mia">✎ {e(p["mi_nota"])}</p>' if p["mi_nota"] else ""
    botones = []
    if p["lat"] is not None and p["lon"] is not None:
        botones.append(f'<a class="enmapa" href="/mapa?foco={quote(p["clave"])}" '
                       f'title="Ver dónde está">📍 Mapa</a>')
    if p["marca"]:
        botones.append(f'<button class="anotar" data-nota="{e(p["mi_nota"] or "")}" '
                       f'data-marca="{e(p["marca"])}" title="Dejar dicho por qué">'
                       f'{"✎ Nota" if p["mi_nota"] else "✎"}</button>')
        botones.append('<button class="limpiar" data-estado="">Quitar marca</button>')
    else:
        botones.append('<button class="si" data-estado="interesa">★ Me interesa</button>')
        botones.append('<button class="no" data-estado="no_interesa">No</button>')
        botones.append('<button class="no" data-estado="no_interesa" data-edificio="1" '
                       'title="Descartar todos los avisos de este edificio">🏢 Edificio, no</button>')
    # Va siempre, incluso sobre una ya marcada: uno se entera de que el aviso
    # murio justo cuando va a verla.
    botones.append('<button class="fin" data-estado="caida" title="El aviso ya no '
                   'existe: se saca del listado">Ya no está</button>')
    clases = "ficha" + (" descartada" if p["marca"] == "no_interesa" else "") + \
        ("" if foto else " sinfoto")
    return f"""<article class="{clases}" data-clave="{e(p["clave"])}">
  <div class="pts {color_puntaje(p["puntaje"])}"><b>{nro(p["puntaje"])}</b><span>pts</span></div>
  {tapa}
  <div class="cuerpo">
    <div class="tit">{titulo_html}</div>
    <div class="meta">{meta}</div>
    <div>{"".join(chips)}</div>
    {nota}
    <ul class="motivos">{lis}</ul>
  </div>
  <div class="precio">
    <b>{usd(p["precio_usd"])}</b>
    <span>{usd(p["precio_m2"]) + "/m²" if p["precio_m2"] else ""}</span>
    <span>{"exp $" + nro(p["expensas_ars"]) if p["expensas_ars"] else ""}</span>
    <div class="acciones">{"".join(botones)}</div>
  </div>
</article>"""


def activos_texto(f) -> str:
    if f["clave"]:
        return ""
    partes = [f["barrio"] or None,
              {"depto": "departamentos", "ph": "PH", "casa": "casas"}.get(f["tipo"]),
              f"{f['amb']} amb" if f["amb"] else None,
              f"hasta {usd(f['precio_max'])}" if f["precio_max"] else None,
              f"rinde {nro(f['rend_min'], 1)}%+" if f["rend_min"] else None,
              f"“{f['q']}”" if f["q"] else None,
              "bajaron de precio" if f["solo_bajadas"] else None]
    return ": " + " · ".join(p for p in partes if p) if any(partes) else ""


def pagina_listado(con, q, csrf) -> str:
    f = filtros(q)
    filas, total = listado(con, f)
    res = resumen(con)
    paginas = -(-total // POR_PAGINA)
    out = [cabecera("Listado", "lista", res, csrf),
           f'<details class="cajon" open><summary>Filtros{e(activos_texto(f))}</summary>'
           f'{form_listado(f, barrios(con))}</details>']
    if f["edificio"]:
        out.append(f'<p class="unasola">Mostrando <b>lo que hay en venta en una misma '
                   f'dirección</b> ({nro(total)}), marcados y sin marcar. Varias unidades en '
                   f'venta en el mismo edificio puede ser un edificio de inversores, un '
                   f'consorcio con problemas o una torre a estrenar: conviene mirarlo antes. '
                   f'<a href="/">Ver el listado completo</a></p>')
    elif f["clave"]:
        out.append('<p class="unasola">Mostrando <b>una sola propiedad</b>, la que viniste '
                   'a ver desde el mapa. <a href="/">Ver el listado completo</a></p>')
    else:
        out.append(f'<p style="color:var(--tinta3);font-size:13.5px;margin:0 0 12px">'
                   f'<b style="color:var(--tinta)">{nro(total)}</b> propiedades '
                   f'{"sin marcar" if f["estado"] == "sin_marcar" else ""}</p>')
    if not filas:
        out.append('<div class="vacio">No hay nada con esos filtros.<br>Probá ampliar el '
                   'rango o <a href="/">limpiar todo</a>.</div>')
    out += [ficha(p) for p in filas]
    if paginas > 1:
        ant = f'<a href="{e(url_con(q, pagina=f["pagina"] - 1))}">← anterior</a>' \
            if f["pagina"] > 1 else ""
        sig = f'<a href="{e(url_con(q, pagina=f["pagina"] + 1))}">siguiente →</a>' \
            if f["pagina"] < paginas else ""
        out.append(f'<div class="paginas">{ant}<span>{f["pagina"]} de {paginas}</span>{sig}</div>')
    out.append('<script src="/static/listado.js"></script>')
    return "".join(out) + PIE


def pagina_mapa(con, q, csrf) -> str:
    f = filtros(q)
    puntos = puntos_mapa(con, f)
    res = resumen(con)
    foco = (q.get("foco") or [""])[0].strip()
    # La propiedad puede no estar en `puntos` (los filtros del mapa son otros y
    # hay un tope): si falta se la busca aparte, o el enlace caeria en un mapa
    # donde justo esa no esta.
    if foco and not any(x["clave"] == foco for x in puntos):
        extra = puntos_mapa(con, {**f, "clave": foco}, 1)
        if extra:
            puntos.insert(0, extra[0])
        else:
            foco = ""        # no existe o no tiene coordenadas
    for x in puntos:
        x["url"] = url_segura(x["url"])
    activos = [f"hasta {usd(f['precio_max'])}" if f["precio_max"] else None,
               f"rinde {nro(f['rend_min'], 1)}%+" if f["rend_min"] else None,
               f"puntaje {int(f['puntaje_min'])}+" if f["puntaje_min"] else None]
    activos = " · ".join(a for a in activos if a)
    sel = lambda k: " selected" if f["estado"] == k else ""
    val = lambda x: e(x) if x else ""
    datos = json.dumps({"puntos": puntos, "foco": foco}, ensure_ascii=False)
    datos = datos.replace("<", "\\u003c")          # que un "</script>" no cierre el bloque
    leaflet = "https://cdnjs.cloudflare.com/ajax/libs"
    head = (f'<link rel="stylesheet" href="{leaflet}/leaflet/1.9.4/leaflet.min.css">'
            f'<link rel="stylesheet" href="{leaflet}/leaflet.markercluster/1.5.3/MarkerCluster.min.css">'
            f'<link rel="stylesheet" href="{leaflet}/leaflet.markercluster/1.5.3/MarkerCluster.Default.min.css">')
    return cabecera("Mapa", "mapa", res, csrf, head) + f"""
<details class="cajon" open><summary>Filtros{": " + e(activos) if activos else ""}</summary>
<form class="filtros" method="get" action="/mapa">
  <div class="campo"><label for="estado">Estado</label>
    <select name="estado" id="estado">
      <option value="sin_marcar"{sel("sin_marcar")}>Sin marcar</option>
      <option value="interesa"{sel("interesa")}>★ Me interesan</option>
      <option value="no_interesa"{sel("no_interesa")}>Descartadas</option>
      <option value="todas"{sel("todas")}>Todas</option></select></div>
  <div class="campo"><label for="precio_max">Precio hasta</label>
    <input type="number" name="precio_max" id="precio_max" step="5000"
           value="{val(f["precio_max"] or "")}" placeholder="USD"></div>
  <div class="campo"><label for="rend_min">Rinde desde</label>
    <input type="number" name="rend_min" id="rend_min" step="0.5"
           value="{val(f["rend_min"] or "")}" placeholder="%"></div>
  <div class="campo"><label for="puntaje_min">Puntaje desde</label>
    <input type="number" name="puntaje_min" id="puntaje_min" step="5"
           value="{val(f["puntaje_min"] or "")}" placeholder="0-100"></div>
  <button type="submit">Filtrar</button>
  <a href="/mapa"><button type="button" class="sec">Limpiar</button></a>
</form></details>
<div class="leyenda">
  <span><span class="bolita" style="background:#1b6b4c"></span>80 o más</span>
  <span><span class="bolita" style="background:#c99a2e"></span>65 a 79</span>
  <span><span class="bolita" style="background:#7b8a99"></span>menos de 65</span>
  <span style="margin-left:auto">{nro(len(puntos))} propiedades en el mapa</span>
</div>
{'<p class="unasola">Centrado en la propiedad que viniste a ver. <a href="/mapa">Ver todo el mapa</a></p>' if foco else ""}
<div id="mapa"></div>
<script type="application/json" id="datos">{datos}</script>
<script src="{leaflet}/leaflet/1.9.4/leaflet.min.js"></script>
<script src="{leaflet}/leaflet.markercluster/1.5.3/leaflet.markercluster.min.js"></script>
<script src="/static/mapa.js"></script>
""" + PIE


# ----------------------------------------------------------------- servidor
def hacer_handler(ruta_base: Path, puerto: int, token: str, log):
    hosts = {f"127.0.0.1:{puerto}", f"localhost:{puerto}"}
    origenes = {f"http://{h}" for h in hosts}

    class Handler(BaseHTTPRequestHandler):
        server_version = "BuscadorLocal"
        sys_version = ""

        def log_message(self, fmt, *args):
            log("  " + (fmt % args))

        # -- respuestas
        def _enviar(self, codigo, cuerpo: bytes, tipo="text/html; charset=utf-8"):
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(cuerpo)))
            self.send_header("Content-Security-Policy", CSP)
            self.send_header("X-Content-Type-Options", "nosniff")
            self.send_header("Referrer-Policy", "no-referrer")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(cuerpo)

        def _texto(self, codigo, texto):
            self._enviar(codigo, texto.encode("utf-8"))

        def _json(self, codigo, datos):
            self._enviar(codigo, json.dumps(datos, ensure_ascii=False).encode("utf-8"),
                         "application/json; charset=utf-8")

        def _host_ok(self):
            if self.headers.get("Host", "") in hosts:
                return True
            self._texto(403, "Host no permitido.")
            return False

        # -- GET
        def do_GET(self):
            if not self._host_ok():
                return
            url = urlsplit(self.path)
            q = parse_qs(url.query, keep_blank_values=True)
            try:
                if url.path.startswith("/static/"):
                    nombre = url.path[len("/static/"):]
                    if nombre not in TIPOS_ESTATICOS:
                        return self._texto(404, "No existe.")
                    return self._enviar(200, (ESTATICOS / nombre).read_bytes(),
                                        TIPOS_ESTATICOS[nombre] + "; charset=utf-8")
                if url.path == "/favicon.ico":
                    return self._enviar(204, b"", "image/x-icon")
                if url.path not in ("/", "/mapa"):
                    return self._texto(404, "No existe.")
                base = abrir(ruta_base)
                try:
                    con = base.con
                    if con.execute("SELECT COUNT(*) FROM ranking").fetchone()[0] == 0:
                        return self._texto(200, pagina_vacia(token))
                    pagina = pagina_listado if url.path == "/" else pagina_mapa
                    return self._texto(200, pagina(con, q, token))
                finally:
                    base.con.close()
            except Exception as exc:                       # noqa: BLE001
                log(f"  ! error en {self.path}: {type(exc).__name__}: {exc}")
                self._texto(500, "Error interno. Mirá la consola donde corre la web.")

        # -- POST
        def do_POST(self):
            if not self._host_ok():
                return
            if urlsplit(self.path).path != "/marcar":
                return self._texto(404, "No existe.")
            # Que otra pagina del navegador no pueda marcar a escondidas: el
            # Origin tiene que ser esta misma web y el token el de este proceso.
            if self.headers.get("Origin", "") not in origenes:
                return self._json(403, {"ok": False, "error": "Origen no permitido"})
            if not hmac.compare_digest(self.headers.get("X-CSRF", ""), token):
                return self._json(403, {"ok": False,
                                        "error": "Sesión vencida, recargá la página"})
            if not self.headers.get("Content-Type", "").startswith("application/json"):
                return self._json(415, {"ok": False, "error": "Se espera JSON"})
            try:
                largo = int(self.headers.get("Content-Length", "0"))
            except ValueError:
                largo = -1
            if not 0 < largo <= MAX_CUERPO:
                return self._json(413, {"ok": False, "error": "Cuerpo inválido"})
            try:
                datos = json.loads(self.rfile.read(largo).decode("utf-8"))
                if not isinstance(datos, dict):
                    raise ValueError("JSON inválido")
            except (ValueError, UnicodeDecodeError):
                return self._json(400, {"ok": False, "error": "JSON inválido"})
            base = abrir(ruta_base)
            try:
                self._json(200, aplicar_marca(base, datos))
            except ValueError as exc:
                self._json(400, {"ok": False, "error": str(exc)})
            except sqlite3.Error as exc:
                log(f"  ! no se pudo guardar la marca: {exc}")
                self._json(500, {"ok": False, "error": "No se pudo guardar"})
            finally:
                base.con.close()

    return Handler


def servir(cfg, puerto=8765, abrir_navegador=False, log=print):
    ruta = Path(cfg["_base"])
    # Crea el esquema (incluida la tabla `ranking`) si la base es nueva.
    abrir(ruta).cerrar()
    token = secrets.token_urlsafe(32)
    # SIEMPRE 127.0.0.1, nunca 0.0.0.0: la web no tiene login.
    try:
        servidor = ThreadingHTTPServer(("127.0.0.1", puerto),
                                       hacer_handler(ruta, puerto, token, log))
    except OSError as exc:
        raise SystemExit(f"  No se pudo abrir el puerto {puerto}: {exc}\n"
                         f"  Probá con otro: python -m inmobiliario web --puerto 8766")
    servidor.daemon_threads = True
    url = f"http://127.0.0.1:{puerto}/"
    log(f"\n  Web local lista en {url}")
    log("  Solo se ve desde esta compu. Ctrl+C para cerrarla.")
    if abrir_navegador:
        threading.Timer(0.5, webbrowser.open, args=(url,)).start()
    try:
        servidor.serve_forever()
    except KeyboardInterrupt:
        log("\n  web cerrada")
    finally:
        servidor.server_close()
