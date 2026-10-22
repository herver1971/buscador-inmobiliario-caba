"""Informe HTML de calibracion.

La primera corrida no notifica nada: encuentra todo el stock historico de los
portales y eso, disparado como avisos, haria el sistema inservible desde el
dia uno. En cambio produce este informe, que sirve para dos cosas:

  1. Ver que trajo y decidir donde poner el umbral de aviso.
  2. Ver el BORDE del filtro (lo que quedo justo afuera), que es lo unico que
     permite ampliar la busqueda con criterio en vez de a ciegas.
"""
from __future__ import annotations

import html
import statistics
from collections import Counter
from datetime import datetime
from pathlib import Path

CSS = """
:root{--tinta:#1a1a1a;--suave:#666;--linea:#dcdcd6;--fondo:#faf9f6;--panel:#fff;
      --acento:#1f3a5f;--bien:#1c6b45;--ojo:#8a5a00;--mal:#8c2f2f}
*{box-sizing:border-box}
body{margin:0;background:var(--fondo);color:var(--tinta);
     font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif}
.wrap{max-width:1080px;margin:0 auto;padding:28px 20px 60px}
h1{font-size:26px;margin:0 0 2px}
.sub{color:var(--suave);font-size:13px;margin-bottom:22px}
h2{font-size:17px;margin:32px 0 10px;padding-bottom:5px;border-bottom:2px solid var(--linea)}
.panel{background:var(--panel);border:1px solid var(--linea);border-radius:8px;
       padding:14px 16px;margin-bottom:14px}
.grid{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}
.kpi{background:var(--panel);border:1px solid var(--linea);border-radius:8px;padding:11px 13px}
.kpi b{display:block;font-size:21px;line-height:1.2}
.kpi span{color:var(--suave);font-size:11.5px;text-transform:uppercase;letter-spacing:.04em}
.aviso{border-left:4px solid var(--ojo);background:#fff8e8;padding:11px 14px;
       border-radius:0 6px 6px 0;margin-bottom:12px}
.aviso.mal{border-color:var(--mal);background:#fdf0f0}
.aviso.bien{border-color:var(--bien);background:#eefaf3}
table{width:100%;border-collapse:collapse;font-size:13px}
th,td{text-align:left;padding:6px 9px;border-bottom:1px solid var(--linea);vertical-align:top}
th{font-size:11.5px;text-transform:uppercase;letter-spacing:.04em;color:var(--suave)}
td.n,th.n{text-align:right;white-space:nowrap;font-variant-numeric:tabular-nums}
.ficha{background:var(--panel);border:1px solid var(--linea);border-radius:8px;
       padding:14px 16px;margin-bottom:11px;display:grid;
       grid-template-columns:64px 1fr auto;gap:14px;align-items:start}
.pts{font-size:25px;font-weight:700;color:var(--acento);line-height:1;text-align:center}
.pts small{display:block;font-size:10px;font-weight:400;color:var(--suave);
           text-transform:uppercase;letter-spacing:.05em;margin-top:3px}
.tit{font-weight:600;font-size:15px;margin-bottom:2px}
.tit a{color:var(--tinta);text-decoration:none}
.tit a:hover{color:var(--acento);text-decoration:underline}
.meta{color:var(--suave);font-size:12.5px;margin-bottom:7px}
.motivos{font-size:12.5px;color:#3a3a3a}
.motivos li{margin-bottom:2px}
.precio{text-align:right;white-space:nowrap}
.precio b{font-size:17px;display:block}
.precio span{color:var(--suave);font-size:12px}
.chip{display:inline-block;font-size:10.5px;padding:1px 7px;border-radius:9px;
      background:#eceae4;color:#444;margin-right:4px;white-space:nowrap}
.chip.ojo{background:#fdf0d5;color:#7a4e00}
.chip.bien{background:#dff3e7;color:#155c3a}
.chip.mal{background:#fadede;color:#8c2f2f}
.barra{height:7px;background:#e8e6e0;border-radius:4px;overflow:hidden;margin-top:5px}
.barra i{display:block;height:100%;background:var(--acento)}
.nota{color:var(--suave);font-size:12.5px}
code{background:#eceae4;padding:1px 5px;border-radius:3px;font-size:12px}
"""


def _e(x):
    return html.escape(str(x if x is not None else ""))


def _dinero(v, pre="USD "):
    return f"{pre}{v:,.0f}".replace(",", ".") if v else "—"


def _ficha(pub, r, fila):
    chips = []
    if fila.get("geo_dudosa"):
        chips.append("<span class='chip ojo'>verificar ubicacion</span>")
    if pub.emprendimiento:
        chips.append("<span class='chip'>emprendimiento</span>")
    if r.rendimiento and r.rendimiento >= 0.075:
        chips.append(f"<span class='chip bien'>rinde {r.rendimiento*100:.1f}%</span>")
    if any("BAJO DE PRECIO" in m for m in r.motivos):
        chips.append("<span class='chip bien'>bajo de precio</span>")
    copias = fila.get("copias", 1)
    if copias > 1:
        otras = " ".join(
            f"<a href='{_e(u)}' target='_blank'>#{i+2}</a>"
            for i, u in enumerate(fila.get("urls_copias", [])[:4]))
        chips.append(f"<span class='chip ojo'>publicada {copias} veces: {otras}</span>")
    chips.append(f"<span class='chip'>{_e(' + '.join(fila.get('fuentes_copias') or [pub.fuente]))}</span>")

    motivos = "".join(f"<li>{_e(m)}</li>" for m in r.motivos if m)
    m2 = f"{pub.m2:.0f} m²" if pub.m2 else "m² s/d"
    return f"""
<div class='ficha'>
  <div><div class='pts'>{r.puntaje:.0f}<small>puntos</small></div>
       <div class='barra'><i style='width:{min(100, r.puntaje)}%'></i></div></div>
  <div>
    <div class='tit'><a href='{_e(pub.url)}' target='_blank'>{_e(pub.titulo[:95] or pub.direccion)}</a></div>
    <div class='meta'>{_e(pub.direccion)} &middot; <b>{_e(pub.barrio)}</b> &middot;
      {_e(pub.tipo)} &middot; {m2} &middot;
      {_e(pub.ambientes) or '?'} amb &middot;
      zona {r.peso_zona:.0f}{f' &rarr; {r.peso_efectivo:.1f}' if r.peso_efectivo > r.peso_zona + .05 else ''}</div>
    <div>{''.join(chips)}</div>
    <ul class='motivos'>{motivos}</ul>
  </div>
  <div class='precio'><b>{_dinero(pub.precio_usd)}</b>
    <span>{_dinero(pub.precio_m2, 'USD ') + '/m²' if pub.precio_m2 else ''}</span>
    <span>{('exp $' + f'{pub.expensas_ars:,.0f}'.replace(',', '.')) if pub.expensas_ars else ''}</span>
  </div>
</div>"""


def escribir(cfg, filas, descartadas, db, usd, directorio: Path) -> Path:
    directorio.mkdir(parents=True, exist_ok=True)
    ruta = directorio / f"informe-{datetime.now():%Y-%m-%d}.html"
    res = db.resumen()
    med_alq = db.medianas_alquiler()[0]
    alq_fuera = db.alquileres_descartados()
    puntajes = [r.puntaje for _, r, _ in filas]
    top_n = cfg["salida"]["top_detallado"]

    p = []
    p.append(f"<!doctype html><html lang='es'><head><meta charset='utf-8'>"
             f"<meta name='viewport' content='width=device-width,initial-scale=1'>"
             f"<title>Informe de calibracion — {datetime.now():%d/%m/%Y}</title>"
             f"<style>{CSS}</style></head><body><div class='wrap'>")
    p.append(f"<h1>Oportunidades de inversion — CABA</h1>"
             f"<div class='sub'>Informe de calibracion &middot; "
             f"{datetime.now():%d/%m/%Y %H:%M} &middot; dolar de referencia "
             f"${usd:,.0f} &middot; {len(filas)} publicaciones puntuadas</div>"
             .replace(",", "."))

    # ---- KPIs
    p.append("<div class='grid'>")
    for etiqueta, valor in [
        ("En venta (activas)", f"{res['venta_activas']:,}".replace(",", ".")),
        ("Alquileres relevados", f"{res['alquiler_activas']:,}".replace(",", ".")),
        ("Con coordenadas", f"{res['con_coordenadas']:,}".replace(",", ".")),
        ("Observaciones de precio", f"{res['observaciones_precio']:,}".replace(",", ".")),
        ("Corridas hechas", res["corridas"]),
    ]:
        p.append(f"<div class='kpi'><b>{valor}</b><span>{etiqueta}</span></div>")
    p.append("</div>")

    # ---- Estado del sistema: que se puede creer y que no
    p.append("<h2>Que tan confiable es este informe todavia</h2>")
    if len(med_alq) < 5:
        p.append("<div class='aviso mal'><b>Todavia no hay base de alquileres.</b> "
                 "Sin ella no se puede estimar rendimiento, que es la metrica "
                 "central de una busqueda para renta. El componente queda en "
                 "neutral y el ranking se apoya en precio, zona y tamano. "
                 "Hacen falta unas cuantas corridas relevando alquileres para "
                 "que esto empiece a ordenar de verdad.</div>")
    else:
        p.append(f"<div class='aviso bien'><b>Base de alquileres: {len(med_alq)} "
                 f"combinaciones barrio/tipo/ambientes.</b> El rendimiento "
                 f"estimado ya es una senal utilizable.</div>")
    if alq_fuera:
        # Si este numero se dispara, algo cambio en los portales: la mediana
        # aguanta unos pocos, no una avalancha.
        p.append(f"<div class='aviso'><b>{alq_fuera} avisos de alquiler quedaron "
                 f"afuera de las medianas por implausibles.</b> Son sobre todo "
                 f"avisos de 'venta y alquiler' donde el portal muestra el precio "
                 f"de venta en el listado de alquileres, y cocheras o precios por "
                 f"noche del lado barato. Se cortan porque de estas medianas sale "
                 f"el rendimiento.</div>")
    p.append("<div class='aviso'><b>El rendimiento estimado es optimista, "
             "y conviene saber por que.</b> Tres sesgos conocidos, todos en la "
             "misma direccion: (1) del lado de la VENTA pedimos los avisos "
             "ordenados por precio ascendente, o sea que miramos la cola barata; "
             "del lado del ALQUILER tomamos una muestra representativa. Comparar "
             "el extremo barato contra la mediana infla el rendimiento de todo. "
             "(2) Son precios de publicacion, no de cierre. (3) Es rendimiento "
             "<i>bruto</i>: no descuenta ABL, seguro, vacancia, comision ni "
             "reparaciones. Para ORDENAR el ranking sirve igual, porque el sesgo "
             "afecta a todos parecido; para decidir cuanto vas a ganar, restale "
             "unos puntos.</div>")

    if res["corridas"] <= 1:
        p.append("<div class='aviso'><b>Primera corrida.</b> Todo lo que aparece "
                 "es stock historico de los portales, no novedades. Las senales "
                 "mas valiosas (bajadas de precio, tiempo publicado) necesitan "
                 "historia: recien se activan a partir de la segunda o tercera "
                 "corrida. Por eso esta corrida no notifica nada.</div>")

    # ---- Salud de las fuentes
    ultima = db.con.execute(
        "SELECT resumen FROM corridas ORDER BY id DESC LIMIT 1").fetchone()
    if ultima:
        import json
        r = json.loads(ultima["resumen"])
        p.append("<h2>Salud de las fuentes</h2><div class='panel'><table>"
                 "<tr><th>Fuente</th><th class='n'>Paginas</th>"
                 "<th class='n'>Avisos</th><th class='n'>Errores</th><th>Estado</th></tr>")
        for f in r.get("fuentes", []):
            estado = ("<span class='chip mal'>CIEGA</span> " + _e(f["detalle"])[:90]
                      if f["ciega"] else "<span class='chip bien'>ok</span>")
            p.append(f"<tr><td>{_e(f['nombre'])}</td><td class='n'>{f['paginas']}</td>"
                     f"<td class='n'>{f['encontradas']}</td>"
                     f"<td class='n'>{f['errores']}</td><td>{estado}</td></tr>")
        p.append("</table></div>")

    # ---- Calibracion: donde poner el umbral
    if puntajes:
        p.append("<h2>Donde poner el umbral de aviso</h2><div class='panel'>")
        cortes = [30, 40, 50, 60, 70, 75, 80, 85, 90]
        p.append("<table><tr><th>Si el umbral fuera…</th>"
                 "<th class='n'>Publicaciones</th><th class='n'>% del total</th>"
                 "<th>Lectura</th></tr>")
        for c in cortes:
            n = sum(1 for x in puntajes if x >= c)
            if n == 0:
                continue
            lectura = ("catarata" if n > 500 else "mucho" if n > 150 else
                       "una tarde de revisar" if n > 60 else "manejable" if n > 20
                       else "selectivo" if n > 5 else "muy selectivo")
            p.append(f"<tr><td><code>puntaje &gt;= {c}</code></td><td class='n'>{n}</td>"
                     f"<td class='n'>{100*n/len(puntajes):.1f}%</td>"
                     f"<td>{lectura}</td></tr>")
        p.append("</table>")
        p.append(f"<p class='nota'>Puntaje maximo alcanzado: "
                 f"<b>{max(puntajes):.0f}</b> &middot; mediana "
                 f"{statistics.median(puntajes):.0f}. Los cortes de arriba son sobre "
                 f"el stock historico completo; una vez en regimen, cada corrida trae "
                 f"solo lo nuevo y lo que cambio de precio, asi que el volumen diario "
                 f"real es mucho menor.</p></div>")

    # ---- Lo mejor, en detalle
    # Las marcadas "no me interesa" salen del listado principal pero NO se
    # pierden: tienen su propia seccion al final. Las de "me interesa" quedan
    # arriba de todo, que es lo que las hace utiles.
    interesa = [f for f in filas if f[2].get("marca") == "interesa"]
    no_interesa = [f for f in filas if f[2].get("marca") == "no_interesa"]
    filas = [f for f in filas if not f[2].get("marca")]

    if interesa:
        p.append(f"<h2>★ Te interesan ({len(interesa)})</h2>")
        for pub, r, fila in interesa:
            p.append(_ficha(pub, r, fila))

    p.append(f"<h2>Las {min(top_n, len(filas))} mejores</h2>")
    for pub, r, fila in filas[:top_n]:
        p.append(_ficha(pub, r, fila))

    # ---- Lo mejor de cada tipo
    # Con un ranking global unico, el segmento de mayor peso se lleva la lista
    # entera: los deptos de Palermo tapan a los PH de Versalles aunque tengan
    # el mismo merito. Estas secciones garantizan que ningun tipo habilitado
    # quede invisible.
    posicion = {id(pub): i + 1 for i, (pub, _, _) in enumerate(filas)}
    otros = [t for t in ("ph", "casa", "depto")
             if any(pub.tipo == t for pub, _, _ in filas[:top_n]) is False
             and any(pub.tipo == t for pub, _, _ in filas)]
    if otros:
        p.append("<h2>Lo mejor de cada tipo que no entro arriba</h2>"
                 "<p class='nota'>El ranking es uno solo, y el segmento de mayor "
                 "peso tiende a copar los primeros puestos. Esto evita que un "
                 "tipo que habilitaste quede invisible en el fondo de la lista.</p>")
    for tipo in otros:
        sub = [f for f in filas if f[0].tipo == tipo][:6]
        if not sub:
            continue
        p.append(f"<div class='panel'><b>{tipo.upper()}</b>"
                 f" <span class='nota'>— {sum(1 for f in filas if f[0].tipo == tipo)} "
                 f"en el ranking; el mejor esta en el puesto "
                 f"{posicion[id(sub[0][0])]} de {len(filas)}</span><table>"
                 "<tr><th class='n'>Puesto</th><th class='n'>Pts</th><th>Direccion</th>"
                 "<th>Barrio</th><th class='n'>m²</th><th class='n'>Amb</th>"
                 "<th class='n'>Precio</th><th class='n'>Rinde</th></tr>")
        for pub, r, fila in sub:
            rinde = f"{r.rendimiento*100:.1f}%" if r.rendimiento else "—"
            p.append(
                f"<tr><td class='n'>#{posicion[id(pub)]}</td>"
                f"<td class='n'><b>{r.puntaje:.0f}</b></td>"
                f"<td><a href='{_e(pub.url)}' target='_blank'>"
                f"{_e(pub.direccion or pub.titulo[:40])}</a></td>"
                f"<td>{_e(pub.barrio)}</td>"
                f"<td class='n'>{f'{pub.m2:.0f}' if pub.m2 else '—'}</td>"
                f"<td class='n'>{pub.ambientes or '—'}</td>"
                f"<td class='n'>{_dinero(pub.precio_usd, '')}</td>"
                f"<td class='n'>{rinde}</td></tr>")
        p.append("</table></div>")

    # ---- El resto: el borde del filtro
    resto = filas[top_n:]
    if resto:
        p.append(f"<h2>El resto ({len(resto)})</h2>"
                 "<p class='nota'>Esto es el borde de tu propio filtro: lo que el "
                 "sistema vio y dejo abajo. Mirarlo es la unica forma de ampliar la "
                 "busqueda con criterio.</p><div class='panel'><table>"
                 "<tr><th class='n'>Pts</th><th>Direccion</th><th>Barrio</th>"
                 "<th>Tipo</th><th class='n'>m²</th><th class='n'>Amb</th>"
                 "<th class='n'>Precio</th><th class='n'>USD/m²</th></tr>")
        for pub, r, fila in resto[:400]:
            p.append(
                f"<tr><td class='n'><b>{r.puntaje:.0f}</b></td>"
                f"<td><a href='{_e(pub.url)}' target='_blank'>{_e(pub.direccion or pub.titulo[:40])}</a></td>"
                f"<td>{_e(pub.barrio)}</td><td>{_e(pub.tipo)}</td>"
                f"<td class='n'>{f'{pub.m2:.0f}' if pub.m2 else '—'}</td>"
                f"<td class='n'>{pub.ambientes or '—'}</td>"
                f"<td class='n'>{_dinero(pub.precio_usd, '')}</td>"
                f"<td class='n'>{f'{pub.precio_m2:,.0f}'.replace(',', '.') if pub.precio_m2 else '—'}</td></tr>")
        p.append("</table>")
        if len(resto) > 400:
            p.append(f"<p class='nota'>… y {len(resto)-400} mas.</p>")
        p.append("</div>")

    # ---- Que se descarto y por que
    if descartadas:
        p.append("<h2>Que se descarto, y por que</h2><div class='panel'>"
                 "<p class='nota'>Si algo de esto te parece mal descartado, es una "
                 "linea en <code>config/exclusiones.yaml</code> o en "
                 "<code>config/barrios.yaml</code>.</p><table>"
                 "<tr><th>Motivo</th><th class='n'>Cantidad</th></tr>")
        for motivo, n in Counter(descartadas).most_common(20):
            p.append(f"<tr><td>{_e(motivo)}</td><td class='n'>{n}</td></tr>")
        p.append("</table></div>")

    p.append("<h2>Como ajustar</h2><div class='panel'><table>"
             "<tr><th>Para…</th><th>Tocar</th></tr>"
             "<tr><td>Que un barrio pese mas o menos</td><td><code>config/barrios.yaml</code></td></tr>"
             "<tr><td>Sumar una facultad, hospital o proyecto</td><td><code>config/pois.yaml</code></td></tr>"
             "<tr><td>Cambiar la franja de precio ideal</td><td><code>config/config.yaml</code> &rarr; <code>precio</code></td></tr>"
             "<tr><td>Que el rendimiento pese mas que el precio</td><td><code>config/config.yaml</code> &rarr; <code>scoring.pesos</code></td></tr>"
             "<tr><td>Sacar un aviso o un edificio entero</td><td><code>config/exclusiones.yaml</code></td></tr>"
             "<tr><td>Agregar un innegociable</td><td><code>config/config.yaml</code> &rarr; <code>filtros_duros</code></td></tr>"
             "</table></div>")

    p.append("</div></body></html>")
    ruta.write_text("".join(p), encoding="utf-8")
    return ruta
