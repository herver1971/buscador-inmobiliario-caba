"""Deja el ranking ya puntuado en la base, para que la web local lo lea.

`armar_ranking` (cli.py) puntua todo en memoria y tarda; la web no puede
repetir ese trabajo en cada pagina. Por eso `informe` guarda aca el resultado,
aplanado a lo que las fichas necesitan mostrar, en la tabla `ranking`.

La tabla es descartable: no tiene nada que no salga de la base de
publicaciones, y se rehace corriendo `informe` de nuevo.
"""
from __future__ import annotations

import json
from datetime import datetime

COLUMNAS = (
    "clave fuente url titulo foto tipo barrio direccion lat lon geo_dudosa "
    "precio_usd precio_m2 expensas_ars m2 ambientes dormitorios antiguedad "
    "emprendimiento puntaje rendimiento rendimiento_temp alquiler_estimado "
    "peso_zona peso_efectivo motivos pois_cerca edificio en_edificio "
    "en_edificio_total edificio_exacto copias urls_copias bajo_precio "
    "precio_anterior vista_primera vista_ultima actualizada").split()


def _fila(pub, res, fila, historial, ahora):
    """Aplana una propiedad puntuada a una fila de la tabla `ranking`."""
    primero = next((p for _, p in historial if p), None)
    ultimo = historial[-1][1] if historial else None
    # Una baja de mas de 3 % desde la primera vez que se la vio.
    bajo = bool(primero and ultimo and ultimo < primero * 0.97)
    return {
        "clave": pub.clave, "fuente": pub.fuente, "url": pub.url,
        "titulo": (pub.titulo or "")[:300], "foto": (pub.foto or "")[:500],
        "tipo": pub.tipo, "barrio": pub.barrio,
        "direccion": (pub.direccion or "")[:200],
        "lat": pub.lat, "lon": pub.lon,
        "geo_dudosa": int(bool(fila.get("geo_dudosa"))),
        "precio_usd": pub.precio_usd, "precio_m2": pub.precio_m2,
        "expensas_ars": pub.expensas_ars, "m2": pub.m2,
        "ambientes": pub.ambientes, "dormitorios": pub.dormitorios,
        "antiguedad": pub.antiguedad, "emprendimiento": int(pub.emprendimiento),
        "puntaje": round(res.puntaje, 2),
        "rendimiento": round(res.rendimiento, 4) if res.rendimiento else None,
        "rendimiento_temp": (round(res.rendimiento_temporario, 4)
                             if res.rendimiento_temporario else None),
        "alquiler_estimado": res.alquiler_estimado,
        "peso_zona": res.peso_zona, "peso_efectivo": round(res.peso_efectivo, 2),
        "motivos": json.dumps([m for m in res.motivos if m], ensure_ascii=False),
        "pois_cerca": json.dumps([[n, d] for n, d, _ in res.pois_cerca],
                                 ensure_ascii=False),
        # Cuantos avisos hay en el mismo edificio y con que id agruparlos.
        "edificio": fila.get("edificio") or pub.clave,
        "en_edificio": fila.get("en_edificio", 1),
        "en_edificio_total": fila.get("en_edificio_total", 1),
        # 1 = el grupo son las coordenadas (el edificio); 0 = se agrupo por
        # direccion, que con la altura redondeada a la centena es la cuadra.
        "edificio_exacto": fila.get("edificio_exacto", 1),
        "copias": fila.get("copias", 1),
        "urls_copias": json.dumps(fila.get("urls_copias") or [], ensure_ascii=False),
        "bajo_precio": int(bajo),
        "precio_anterior": primero if bajo else None,
        "vista_primera": fila.get("vista_primera"),
        "vista_ultima": fila.get("vista_ultima"),
        "actualizada": ahora,
    }


def guardar_ranking(cfg, db, filas) -> int:
    """Reemplaza la tabla `ranking` con las filas puntuadas. Devuelve cuantas."""
    from .cli import _agrupar_edificios      # import tardio: cli importa este modulo

    desde = float((cfg.get("salida") or {}).get("web_puntaje_minimo") or 0)
    muertos = {c["clave"] for c in db.cargar_caidas()}
    publicables = [(p, r, f) for p, r, f in filas
                   if r.puntaje >= desde and p.clave not in muertos]

    # El conteo por edificio se rehace sobre lo que efectivamente se muestra: si
    # se dejara el del ranking entero, el chip diria "9 en venta aca" y el click
    # traeria 3. El total (sobre todo lo en venta) no depende de los filtros.
    edificios = _agrupar_edificios([p for p, _, _ in publicables])
    for p, _, f in publicables:
        f["edificio"], f["en_edificio"], _, _ = edificios.get(
            p.clave, (p.clave, 1, 1, True))

    ahora = datetime.now().isoformat(timespec="seconds")
    lote = [_fila(p, r, f, db.historial_precio(p.clave), ahora)
            for p, r, f in publicables]

    marcas = ",".join("?" * len(COLUMNAS))
    with db.con:
        db.con.execute("DELETE FROM ranking")
        db.con.executemany(
            f"INSERT INTO ranking ({','.join(COLUMNAS)}) VALUES ({marcas})",
            [tuple(fila[c] for c in COLUMNAS) for fila in lote])
    return len(lote)
