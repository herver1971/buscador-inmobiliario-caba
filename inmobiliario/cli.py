"""Linea de comandos.

    python -m inmobiliario recolectar     # recorre los portales y guarda
    python -m inmobiliario informe        # arma el HTML de calibracion
    python -m inmobiliario estado         # que hay en la base
    python -m inmobiliario web            # listado y mapa en http://127.0.0.1:8765
"""
from __future__ import annotations

import argparse
import sys
import time
from collections import Counter
from datetime import datetime
from pathlib import Path

from . import informe as mod_informe
from .config import cargar, cotizacion, plan_de_busqueda
from .db import Base, USUARIO
from .ranking import guardar_ranking
from .geo import Geocodificador, PuntosDeInteres, en_zona
from .scoring import Evaluador

FUENTES = {}


def _cargar_fuentes():
    from .fuentes.argenprop import Argenprop
    from .fuentes.mercadolibre import MercadoLibre
    from .fuentes.zonaprop import Zonaprop
    # Zonaprop primero: es la unica que trae coordenadas exactas, asi que sus
    # datos son los que mandan cuando una propiedad aparece en varios portales.
    FUENTES.update({"zonaprop": Zonaprop, "mercadolibre": MercadoLibre,
                    "argenprop": Argenprop})


def log(msg=""):
    print(msg, flush=True)


# ---------------------------------------------------------------- recolectar
def recolectar(cfg, args):
    _cargar_fuentes()
    inicio = datetime.now().isoformat(timespec="seconds")
    db = _base(cfg)
    usd = cotizacion(cfg, log)
    geo = Geocodificador(db)

    plan = plan_de_busqueda(cfg)
    if args.solo_barrio:
        plan = [p for p in plan if p[2].lower() == args.solo_barrio.lower()]
    if args.limite:
        plan = plan[: args.limite]
    log(f"  plan: {len(plan)} busquedas")

    # La normalizacion y el guardado ocurren DESPUES de cada busqueda, no al
    # final: una corrida larga que se corta no puede perder horas de trabajo,
    # y ademas asi se ve el avance en la base mientras corre.
    estados, descartes = Counter(), Counter()
    llevadas = [0]

    def guardar_lote(publicaciones, tipo, operacion, barrio):
        for pub in publicaciones:
            if pub.precio and pub.moneda == "USD":
                pub.precio_usd = pub.precio
            elif pub.precio and pub.moneda == "ARS":
                pub.precio_usd = pub.precio / usd

            # Geocodificacion DIFERIDA: aca solo se ubica por barrio, que es
            # gratis. Resolver la direccion exacta de 17.000 publicaciones para
            # que 16.000 queden en el fondo del ranking es tiempo tirado; el
            # informe geocodifica despues solo las que puntuan bien.
            if args.geocodificar and pub.operacion == "venta" and pub.direccion:
                pub.lat, pub.lon = geo(pub.direccion)

            ubic = en_zona(pub.lat, pub.lon, pub.barrio, cfg)
            if not ubic.dentro:
                descartes[ubic.motivo.split("(")[0].strip()] += 1
                continue
            estados[db.guardar(pub, ubic)] += 1
        db.con.commit()
        llevadas[0] += len(publicaciones)
        log(f"    {barrio:22} {tipo:5} {operacion:8} -> {len(publicaciones):4} "
            f"(acumulado {llevadas[0]})")

    reportes = []
    presupuesto = cfg["recoleccion"].get("pedidos_max_por_corrida", 10**6)
    for nombre, Clase in FUENTES.items():
        conf = cfg["fuentes"].get(nombre, {})
        if not conf.get("activa"):
            continue
        if args.solo_fuente and args.solo_fuente != nombre:
            continue
        log(f"\n  == {nombre} ==")
        f = Clase(cfg, log)
        f.presupuesto = presupuesto
        try:
            f.recolectar(plan, al_encontrar=guardar_lote)
        except Exception as e:
            import traceback
            log(f"    ! error inesperado: {type(e).__name__}: {e}")
            log(traceback.format_exc(limit=3))
            if getattr(f, "reporte", None) is not None:
                f.reporte.ciega, f.reporte.detalle = True, f"{type(e).__name__}: {e}"
        presupuesto = max(0, presupuesto - f.reporte.paginas - f.reporte.errores)
        if getattr(f, "reporte", None) is not None:
            reportes.append(f.reporte)
        log(f"    presupuesto restante: {presupuesto} pedidos")

    for r in reportes:
        db.marcar_inactivas(r.nombre)

    resumen = {
        "estados": dict(estados),
        "descartes_geograficos": dict(descartes),
        "geocodificaciones": geo.consultas,
        "geocodificacion_diferida": not args.geocodificar,
        "cotizacion": usd,
        "fuentes": [{"nombre": r.nombre, "paginas": r.paginas,
                     "encontradas": r.encontradas, "errores": r.errores,
                     "ciega": r.ciega, "detalle": r.detalle} for r in reportes],
    }
    db.registrar_corrida(inicio, resumen)

    log(f"\n  nuevas={estados['nueva']}  bajaron={estados['bajo_precio']}  "
        f"subieron={estados['subio']}  sin cambio={estados['igual']}")
    log(f"  descartadas por ubicacion: {sum(descartes.values())}")
    for k, v in descartes.most_common(5):
        log(f"      {v:5}  {k}")
    log(f"  consultas al geocodificador: {geo.consultas}")
    db.cerrar()
    return resumen


# -------------------------------------------------------------------- informe
def armar_ranking(cfg, db, log=log, geocodificar=True):
    """Puntua todo lo activo en venta y devuelve (filas ordenadas, descartadas).

    Lo usan tanto el informe como el aviso de Telegram, asi que el criterio es
    exactamente el mismo en los dos lados: lo que ves en el informe es lo que
    te va a llegar al telefono.
    """
    usd = cotizacion(cfg, log)
    geo = Geocodificador(db)
    pois = PuntosDeInteres(cfg["pois"], geo, cfg["scoring"]["bonus_poi_max"])
    # Las cotas de sanidad van de config: lo que entra en las medianas decide
    # el rendimiento, que es el componente que mas pesa en el puntaje.
    plaus = cfg["scoring"].get("alquiler_plausible") or {}
    cotas = (plaus.get("min_usd"), plaus.get("max_usd"))
    med_alq = db.medianas_alquiler(minimo_usd=cotas[0], maximo_usd=cotas[1])
    med_m2 = db.medianas_precio_m2()
    fuera = db.alquileres_descartados(*cotas)
    log(f"  medianas de alquiler: {len(med_alq[0])} vacio + {len(med_alq[1])} amoblado"
        f"{f' ({fuera} alquileres implausibles afuera)' if fuera else ''}")
    e = cfg["scoring"]["expensas"]
    med_exp = db.medianas_expensas_m2(
        tuple(e["bandas_m2"]), e["muestra_minima"], e["recorte_percentil"],
        (e["implausible_bajo"], e["implausible_alto"]), e["dispersion_maxima"])
    log(f"  medianas de expensas por m2: {len(med_exp[0])} celdas "
        f"(barrio x tipo x tamano), {len(med_exp[1])} barrios")
    ev = Evaluador(cfg, med_alq, med_m2, pois, usd,
                   alquiler_m2=db.medianas_alquiler_m2(minimo_usd=cotas[0],
                                                       maximo_usd=cotas[1]),
                   expensas_m2=med_exp)

    def puntuar(registros):
        filas, descartadas = [], Counter()
        for r in registros:
            pub = _fila_a_pub(r)
            peso = cfg["barrios"].get(pub.barrio, {}).get(pub.tipo, 0)
            res = ev.evaluar(pub, peso, db.historial_precio(r["clave"]),
                             r["vista_primera"])
            if res.descartada:
                descartadas[res.descartada] += 1
                continue
            filas.append((pub, res, dict(r)))
        return filas, descartadas

    filas, descartadas = puntuar(db.activas("venta"))
    filas.sort(key=lambda x: -x[1].puntaje)

    # Geocodificacion selectiva: solo los mejores, y solo los que no traen
    # coordenadas de origen (Zonaprop ya las trae todas).
    if geocodificar:
        tope = cfg["salida"].get("geocodificar_top", 800)
        pendientes = [(pub, fila) for pub, res, fila in filas[:tope]
                      if fila.get("lat") is None and (fila.get("direccion") or "")]
        if pendientes:
            log(f"  geocodificando los {len(pendientes)} mejores sin coordenadas...")
            resueltas = fuera = 0
            for pub, fila in pendientes:
                lat, lon = geo(pub.direccion)
                if lat is None:
                    continue
                ubic = en_zona(lat, lon, pub.barrio, cfg)
                db.con.execute(
                    "UPDATE publicaciones SET lat=?, lon=?, geo_confianza=?, "
                    "geo_motivo=?, geo_dudosa=?, activa=? WHERE clave=?",
                    (lat, lon, ubic.confianza, ubic.motivo, int(ubic.dudosa),
                     int(ubic.dentro), fila["clave"]))
                resueltas += 1
                fuera += (not ubic.dentro)
            db.con.commit()
            log(f"  resueltas {resueltas}; {fuera} quedaron fuera de la zona")
            filas, descartadas = puntuar(db.activas("venta"))

    filas = _fusionar_duplicados(filas)
    filas.sort(key=lambda x: -x[1].puntaje)

    edificios = _agrupar_edificios([pub for pub, _, _ in filas],
                                   universo=[_fila_a_pub(r) for r in db.activas("venta")])
    for pub, res, fila in filas:
        ident, cuantos, total, exacto = edificios.get(pub.clave,
                                                       (pub.clave, 1, 1, True))
        fila["edificio"] = ident
        fila["en_edificio"] = cuantos
        fila["en_edificio_total"] = total
        fila["edificio_exacto"] = int(exacto)

    # Las marcas viajan con cada fila para que el informe las muestre. No se
    # filtra nada aca: quien consume decide que hacer con cada estado.
    marca_de = _marcas_activas(cfg, db)
    for pub, res, fila in filas:
        fila["marca"] = marca_de(pub)
    return filas, descartadas, usd


def construir_informe(cfg, args):
    db = _base(cfg)
    filas, descartadas, usd = armar_ranking(cfg, db)
    salida = cfg["_raiz"] / cfg["salida"]["directorio"]
    ruta = mod_informe.escribir(cfg, filas, descartadas, db, usd, salida)
    guardados = guardar_ranking(cfg, db, filas)
    log(f"  ranking para la web local: {guardados} propiedades")
    log(f"\n  {len(filas)} propiedades puntuadas, {sum(descartadas.values())} descartadas")
    log(f"  informe: {ruta}")
    db.cerrar()
    return ruta


# Cuando la misma propiedad aparece en varios portales, los datos de Zonaprop
# son los que mandan: trae coordenadas exactas, expensas y antiguedad, cosas
# que MercadoLibre directamente no publica.
PRIORIDAD_FUENTE = {"zonaprop": 0, "argenprop": 1, "mercadolibre": 2}


def _fusionar_duplicados(filas, metros=60):
    """Colapsa la misma propiedad publicada varias veces.

    Dos pasadas, de la mas segura a la mas arriesgada:

    1. Por huella (direccion normalizada + superficie + ambientes). Sirve
       dentro de un mismo portal, donde la direccion se escribe igual.

    2. Por CERCANIA GEOGRAFICA: dos avisos a menos de 60 m, con el mismo precio
       y los mismos ambientes, son casi con seguridad el mismo departamento.
       Es lo unico que funciona entre portales, donde la direccion se escribe
       distinto ("Borges al 2400" vs "BORGES 2400") y las superficies
       declaradas no coinciden.

    Lo fusionado no se esconde: se muestra con el contador de copias y todos
    los links, asi se ve si la fusion estuvo bien.
    """
    from .geo import haversine_m

    def elegir(copias):
        # Gana el de mejor fuente; a igualdad, el de mayor puntaje.
        copias.sort(key=lambda c: (PRIORIDAD_FUENTE.get(c[0].fuente, 9), -c[1].puntaje))
        pub, res, fila = copias[0]
        fila = dict(fila)
        fila["copias"] = len(copias)
        fila["urls_copias"] = [c[0].url for c in copias[1:]]
        fila["fuentes_copias"] = sorted({c[0].fuente for c in copias})
        return (pub, res, fila)

    def mismas_expensas(a, b, tol=0.25):
        """Dos unidades del mismo edificio pagan expensas parecidas.

        La que no las publica no separa: no saber no es saber que son
        distintas. Pero 120.000 contra 80.000 en la misma direccion son dos
        departamentos, no el mismo con dos precios.
        """
        ea, eb = a[0].expensas_ars, b[0].expensas_ars
        if not ea or not eb:
            return True
        return abs(ea - eb) / max(ea, eb) <= tol

    # --- Pasada 1: huella exacta, separando por expensas ---
    # La huella es direccion + superficie redondeada + ambientes, y en un
    # edificio lleno de monoambientes de 25 m2 eso es la misma huella para
    # unidades distintas. Las expensas son lo que las distingue.
    grupos = {}
    for item in filas:
        grupos.setdefault(item[0].huella, []).append(item)
    etapa1 = []
    for g in grupos.values():
        subgrupos = []
        # Primero las que declaran expensas: asi cada subgrupo queda anclado en
        # un valor conocido y las que no las publican caen en el primero que
        # las acepte, en vez de anclar un subgrupo con un dato que no existe.
        for item in sorted(g, key=lambda i: i[0].expensas_ars is None):
            for sub in subgrupos:
                if all(mismas_expensas(item, otro) for otro in sub):
                    sub.append(item)
                    break
            else:
                subgrupos.append([item])
        etapa1 += [elegir(sub) for sub in subgrupos]

    # --- Pasada 2: misma ubicacion, mismo precio, mismos ambientes ---
    cubos, sueltas = {}, []
    for item in etapa1:
        pub = item[0]
        if pub.lat is None or not pub.precio_usd:
            sueltas.append(item)
            continue
        # El cubo acota las comparaciones; el chequeo fino es por distancia.
        clave = (round(pub.precio_usd / 1000), pub.ambientes or 0)
        cubos.setdefault(clave, []).append(item)

    salida = list(sueltas)
    for candidatos in cubos.values():
        pendientes = list(candidatos)
        while pendientes:
            base = pendientes.pop(0)
            grupo = [base]
            resto = []
            for otro in pendientes:
                d = haversine_m(base[0].lat, base[0].lon, otro[0].lat, otro[0].lon)
                # La superficie tiene que ser compatible: en un mismo edificio
                # conviven unidades distintas al mismo precio, y fusionar un
                # 31 m2 con un 40 m2 esconde que el segundo es mejor negocio.
                a, b = base[0].m2, otro[0].m2
                m2_compatible = (not a or not b or
                                 abs(a - b) <= 0.15 * max(a, b))
                if d <= metros and m2_compatible:
                    grupo.append(otro)
                else:
                    resto.append(otro)
            pendientes = resto
            if len(grupo) == 1:
                salida.append(base)
            else:
                # Se conservan los contadores que ya traia cada uno de la pasada 1
                copias = sum(g[2].get("copias", 1) for g in grupo)
                urls = [u for g in grupo for u in g[2].get("urls_copias", [])]
                urls += [g[0].url for g in grupo[1:]]
                fuentes = sorted({f for g in grupo
                                  for f in (g[2].get("fuentes_copias") or [g[0].fuente])})
                elegido = elegir([(g[0], g[1], g[2]) for g in grupo])
                elegido[2]["copias"] = copias
                elegido[2]["urls_copias"] = urls
                elegido[2]["fuentes_copias"] = fuentes
                salida.append(elegido)
    return salida


def _base(cfg):
    """La base de datos, siempre abierta igual."""
    return Base(cfg["_base"], USUARIO)


def _fila_a_pub(r):
    from .modelo import Publicacion
    return Publicacion(
        fuente=r["fuente"], id_fuente=r["id_fuente"], url=r["url"],
        operacion=r["operacion"], tipo=r["tipo"], titulo=r["titulo"] or "",
        barrio=r["barrio"] or "", direccion=r["direccion"] or "",
        precio_usd=r["precio_usd"], moneda=r["moneda"] or "USD",
        expensas_ars=r["expensas_ars"], m2_cubiertos=r["m2_cubiertos"],
        m2_totales=r["m2_totales"], ambientes=r["ambientes"],
        dormitorios=r["dormitorios"], antiguedad=r["antiguedad"],
        emprendimiento=bool(r["emprendimiento"]), descripcion=r["descripcion"] or "",
        foto=(r["foto"] if "foto" in r.keys() else "") or "",
        lat=r["lat"], lon=r["lon"],
    )


def regeocodificar(cfg, args):
    """Reintenta las publicaciones que quedaron sin coordenadas.

    Util despues de mejorar la limpieza de direcciones: los fallos anteriores
    quedaron cacheados en negativo y hay que purgarlos para que se reintenten.
    """
    db = _base(cfg)
    borradas = db.con.execute("DELETE FROM geocache WHERE lat IS NULL").rowcount
    db.con.commit()
    log(f"  cache de fallos purgada: {borradas} direcciones")

    geo = Geocodificador(db)
    pendientes = db.con.execute(
        "SELECT clave, direccion, barrio FROM publicaciones "
        "WHERE activa=1 AND operacion='venta' AND lat IS NULL AND direccion != ''"
    ).fetchall()
    log(f"  a reintentar: {len(pendientes)}")

    resueltas = 0
    for r in pendientes:
        lat, lon = geo(r["direccion"])
        if lat is None:
            continue
        ubic = en_zona(lat, lon, r["barrio"], cfg)
        db.con.execute(
            "UPDATE publicaciones SET lat=?, lon=?, geo_confianza=?, geo_motivo=?, "
            "geo_dudosa=?, activa=? WHERE clave=?",
            (lat, lon, ubic.confianza, ubic.motivo, int(ubic.dudosa),
             int(ubic.dentro), r["clave"]))
        resueltas += 1
    db.con.commit()

    total = db.con.execute("SELECT COUNT(*) FROM publicaciones WHERE activa=1 "
                           "AND operacion='venta'").fetchone()[0]
    con_coord = db.con.execute("SELECT COUNT(*) FROM publicaciones WHERE activa=1 "
                               "AND operacion='venta' AND lat IS NOT NULL").fetchone()[0]
    log(f"  resueltas ahora: {resueltas}")
    log(f"  cobertura de coordenadas: {con_coord}/{total} "
        f"({100*con_coord/max(1,total):.0f}%)")
    db.cerrar()


def _clave_edificio(pub):
    """Que agrupa dos avisos en el mismo edificio. None = no se puede saber."""
    from .geo import clave_direccion
    if pub.lat is not None and pub.lon is not None:
        return ("geo", round(pub.lat, 4), round(pub.lon, 4))
    cd = clave_direccion(pub.direccion)
    return ("dir",) + cd if cd else None


def _agrupar_edificios(pubs, universo=None):
    """Junta los avisos que estan en la misma direccion.

    Para que? Tres o cuatro departamentos en venta en el mismo edificio es un
    dato: puede ser un edificio de inversores, puede ser un consorcio con
    problemas, puede ser una torre a estrenar. Sea lo que sea, conviene verlo
    ANTES de enamorarse de uno.

    Se cuenta sobre las FICHAS ya deduplicadas y ya filtradas, no sobre los
    avisos crudos. Es decir: cuenta lo que uno puede abrir. Un edificio donde
    tres unidades quedaron afuera por caras va a decir menos de las que hay,
    pero el numero que muestra nunca promete algo que el click no cumple, y
    eso importa mas que el conteo perfecto.

    Manda la coordenada redondeada (~11 m), que es el edificio de verdad; la
    direccion (calle + centena de la altura) es el respaldo para la mitad de
    los avisos que no traen coordenadas.

    Los dos criterios a la vez, unidos por transitividad, NO sirven: la altura
    redondeada a la centena es media cuadra, asi que un aviso sin coordenadas
    encadenaba entre si todos los edificios de la cuadra y en una avenida
    terminaba diciendo "18 en venta acá" para media Rivadavia. Se prefiere
    quedarse corto: mejor no avisar que avisar de un edificio que no existe.

    """
    grupos = {}
    for pub in pubs:
        clave = _clave_edificio(pub)
        if clave is None:
            continue                     # sin direccion ni coordenadas, sin edificio
        grupos.setdefault(clave, []).append(pub.clave)

    # El total sale del universo entero —todas las ventas activas— y no de las
    # fichas: un edificio con nueve en venta de las que solo tres pasan los
    # filtros dice algo (a estrenar, o algo raro en el consorcio) que un "3" a
    # secas esconde. Son avisos, no unidades: los duplicados de una misma
    # unidad inflan un poco, y es preferible a inventar una dedup a medias.
    totales = {}
    for pub in (universo or []):
        clave = _clave_edificio(pub)
        if clave is not None:
            totales[clave] = totales.get(clave, 0) + 1

    # El id del edificio es la menor de sus claves: estable entre corridas
    # mientras ese aviso siga publicado, y no hace falta guardar nada.
    # Con coordenadas el grupo es el edificio; por direccion es la cuadra, que
    # es otra cosa y hay que decirlo: "28 en venta" en Lavalle al 4000 son 28
    # en la cuadra, no 28 en un edificio.
    salida = {}
    for clave, claves in grupos.items():
        ident, n = min(claves), len(claves)
        for c in claves:
            salida[c] = (ident, n, max(totales.get(clave, n), n), clave[0] == "geo")
    return salida


def _marcas_activas(cfg, db):
    """Devuelve una funcion que dice como esta marcada una publicacion.

    Mira las marcas del usuario (hoy el unico): las que se hicieron desde
    Telegram y desde la web local viven en la misma tabla.

    Contempla que la MISMA propiedad reaparezca publicada por otra
    inmobiliaria o en otro portal: se compara por firma geografica igual que
    en la deduplicacion, no solo por id. Sin eso, marcar seria inutil: al dia
    siguiente te volveria a aparecer con otro numero de aviso.
    """
    from .geo import haversine_m, normalizar
    marcas = [m for m in db.cargar_marcas() if m["usuario"] == db.usuario]

    def estado_de(pub):
        for m in marcas:
            if m["clave"] == pub.clave:
                return m["estado"]
            if m["edificio"] and m["direccion"] and pub.direccion:
                if normalizar(m["direccion"]) == normalizar(pub.direccion):
                    return m["estado"]
            if (m["lat"] and pub.lat and m["precio_usd"] and pub.precio_usd
                    and abs(m["precio_usd"] - pub.precio_usd) < 500
                    and (m["ambientes"] or 0) == (pub.ambientes or 0)
                    and haversine_m(m["lat"], m["lon"], pub.lat, pub.lon) <= 60):
                return m["estado"]
        return None
    return estado_de


def avisar(cfg, args):
    """Manda por Telegram lo nuevo que supere el umbral."""
    from .telegram import Bot, credenciales, ficha, botones, TOPE_PIE

    tg = (credenciales(cfg["_raiz"]).get("telegram") or {})
    # --simular no necesita credenciales: sirve para ver que se enviaria antes
    # de tener el bot, o para probar un cambio de umbral sin molestar.
    if not args.simular and (not tg.get("token") or not tg.get("chat_id")):
        log("  Falta configurar el bot: corre 'python -m inmobiliario telegram'")
        return
    bot = Bot(tg.get("token"), tg.get("chat_id")) if tg.get("token") else None

    db = _base(cfg)
    filas, _, _ = armar_ranking(cfg, db, log)
    marca_de = _marcas_activas(cfg, db)

    conf = cfg["notificacion"]
    umbral = args.umbral if args.umbral is not None else conf["umbral"]

    candidatas = []
    for pub, res, fila in filas:
        # Una propiedad ya marcada (en cualquiera de los dos sentidos) no
        # vuelve a avisar: si dijiste "me interesa", ya la viste.
        if db.ya_notificada(pub.clave) or marca_de(pub):
            continue
        bajo = any(m.startswith("BAJO DE PRECIO") for m in res.motivos)
        # Una bajada de precio avisa aunque no llegue al umbral: es la senal
        # mas valiosa y no la publica ningun portal.
        if res.puntaje >= umbral or (bajo and conf.get("incluir_bajadas_de_precio")):
            candidatas.append((pub, res, fila, bajo))

    if not candidatas:
        log("  nada nuevo que avisar")
        db.cerrar()
        return

    tope = conf.get("maximo_por_aviso", 12)
    lote, resto = candidatas[:tope], len(candidatas) - tope

    if args.simular:
        log(f"\n  [SIMULACION] se enviarian {len(lote)} avisos"
            f"{f' (y quedan {resto} en cola)' if resto > 0 else ''}:\n")
        for pub, res, fila, bajo in lote:
            log(ficha(pub, res, fila, umbral).replace("<b>", "").replace("</b>", "")
                .replace("<i>", "").replace("</i>", ""))
            log("")
        db.cerrar()
        return

    bajadas = sum(1 for c in lote if c[3])
    cabecera = f"<b>{len(lote)} oportunidad{'es' if len(lote) != 1 else ''}</b>"
    if bajadas:
        cabecera += f" · {bajadas} bajaron de precio"
    if resto > 0:
        cabecera += f"\n<i>Hay {resto} más por encima del umbral; se mandan en la próxima.</i>"
    bot.mensaje(cabecera)

    enviadas = 0
    for pub, res, fila, bajo in lote:
        try:
            # Se guarda el id del mensaje: es lo que despues permite contestar
            # el aviso con texto y que la nota caiga en esta propiedad.
            if pub.foto:
                enviado = bot.foto(pub.foto, ficha(pub, res, fila, umbral, TOPE_PIE),
                                   botones(pub), log)
            else:
                enviado = bot.mensaje(ficha(pub, res, fila, umbral), botones(pub))
            db.marcar_notificada(pub.clave, res.puntaje,
                                 (enviado or {}).get("message_id"))
            enviadas += 1
            time.sleep(0.5)          # Telegram limita a ~30 mensajes por segundo
        except Exception as e:
            log(f"  ! no se pudo enviar {pub.clave}: {e}")
    log(f"  enviadas {enviadas} de {len(candidatas)} candidatas")
    db.cerrar()


def _anotar_desde_telegram(cfg, db, bot, msg):
    """Contestar un aviso con texto guarda ese texto como nota.

    Es la unica forma razonable de escribir desde Telegram: los botones no
    reciben texto, y un comando con la clave adentro seria pedirle a alguien
    que copie 'zonaprop:59162073' a mano. Contestar el aviso ya dice de que
    propiedad se habla.
    """
    respondido = (msg.get("reply_to_message") or {}).get("message_id")
    texto = (msg.get("text") or "").strip()
    if not respondido or not texto:
        return
    clave = db.clave_de_mensaje(respondido)
    if not clave:
        bot.mensaje("No sé de qué aviso me estás hablando: contestá el mensaje "
                    "de la propiedad y te lo guardo como nota.")
        return
    de = msg.get("from") or {}
    usuario = db.usuario
    if db.anotar(clave, usuario, texto):
        bot.mensaje(f"✎ Anotado: <i>{texto[:200]}</i>")
        log(f"  nota en {clave} por {de.get('username') or '?'}")
    else:
        # La nota vive en la marca: sin marca no tiene donde ir.
        bot.mensaje("Marcala primero (★ o ✖) y despues contestame el motivo: "
                    "la nota va pegada a la marca.")
        log(f"  nota sin marca en {clave}: no se guardo")


def escuchar(cfg, args):
    """Atiende los botones de descartar. Corre en primer plano hasta Ctrl-C."""
    from .telegram import Bot, credenciales

    tg = (credenciales(cfg["_raiz"]).get("telegram") or {})
    if not tg.get("token"):
        log("  Falta configurar el bot: corre 'python -m inmobiliario telegram'")
        return
    bot = Bot(tg["token"], tg["chat_id"])
    db = _base(cfg)
    log("  escuchando botones (Ctrl-C para salir)...")

    offset = None
    try:
        while True:
            for u in bot.actualizaciones(offset=offset, espera=25):
                offset = u["update_id"] + 1
                if u.get("message"):
                    _anotar_desde_telegram(cfg, db, bot, u["message"])
                cb = u.get("callback_query")
                if not cb:
                    continue
                accion, _, clave = (cb.get("data") or "").partition("|")
                from .telegram import ACCIONES
                if accion not in ACCIONES:
                    bot.responder_boton(cb["id"], "Botón desconocido")
                    continue
                estado, edificio = ACCIONES[accion]

                fila = db.con.execute(
                    "SELECT * FROM publicaciones WHERE clave=?", (clave,)).fetchone()
                if not fila:
                    bot.responder_boton(cb["id"], "No la encuentro en la base")
                    continue
                pub = _fila_a_pub(fila)

                de = cb.get("from") or {}
                quien = de.get("username") or de.get("first_name")

                # Un aviso caido no es una marca de nadie: es un hecho sobre el
                # aviso, vale para todos y saca la publicacion de circulacion.
                if estado == "caida":
                    db.marcar_caida(clave, quien=quien)
                else:
                    db.marcar(pub, estado, quien=quien,
                              origen="telegram", edificio=edificio)

                if estado == "interesa":
                    aviso, icono = "Guardada en «me interesa»", "★"
                elif estado == "caida":
                    aviso, icono = "El aviso ya no existe: fuera del listado", "🗑"
                elif edificio:
                    aviso, icono = f"Edificio descartado: {pub.direccion}", "🏢"
                else:
                    aviso, icono = "Marcada «no me interesa»", "✖"
                bot.responder_boton(cb["id"], aviso)

                # La propiedad no se pierde, solo queda marcada: se agrega
                # una linea al pie con quien la marco y se sacan los botones de
                # marcar. "Ver aviso" queda, que es el unico que sigue sirviendo.
                msg = cb.get("message") or {}
                cuerpo = msg.get("text") or msg.get("caption")
                if msg.get("message_id") and cuerpo:
                    firma = f" · {quien}" if quien else ""
                    bot.editar(
                        msg["chat"]["id"], msg["message_id"],
                        f"{cuerpo}\n\n{icono} <b>{aviso}</b>{firma}",
                        botones=[[{"text": "Ver aviso", "url": pub.url}]],
                        es_foto=bool(msg.get("photo")))
                log(f"  {clave} -> {estado}"
                    f"{' (edificio)' if edificio else ''} por {quien or '?'}"
                    )
    except KeyboardInterrupt:
        log("\n  listo")
    finally:
        db.cerrar()


def desmarcar_una(cfg, args):
    """Saca la marca de una propiedad, y la caida si la tenia.

    Es como se deshace un "ya no esta": desde el navegador no hay forma, porque
    la ficha desaparece del listado y con ella el boton de quitar la marca.
    """
    db = _base(cfg)
    try:
        for m in db.con.execute(
                "SELECT usuario, estado, quien, fecha FROM marcas WHERE clave=?",
                (args.clave,)):
            log(f"  marca: {m['estado']} por {m['quien'] or '?'} el {m['fecha']}")
        caida = db.con.execute("SELECT fecha, quien FROM caidas WHERE clave=?",
                               (args.clave,)).fetchone()
        if caida:
            log(f"  marcada como caida por {caida['quien'] or '?'} "
                f"el {caida['fecha']}")
        borradas = db.desmarcar(args.clave)
        log(f"  borradas: {borradas + db.levantar_caida(args.clave)}")
        if caida and db.reactivar(args.clave):
            log("  la publicacion vuelve al ranking; se sube en la proxima corrida")
    finally:
        db.cerrar()


def estado(cfg, args):
    db = _base(cfg)
    for k, v in db.resumen().items():
        log(f"  {k:24} {v:>8,}")
    med = db.medianas_alquiler()[0]
    log(f"  {'medianas_alquiler':24} {len(med):>8,}")
    if med:
        log("\n  Alquiler mediano (USD/mes) por barrio/tipo/ambientes:")
        for (b, t, a), v in sorted(med.items())[:15]:
            log(f"      {b:20} {t:6} {a} amb  USD {v:>7,.0f}")
    db.cerrar()


def consola_utf8():
    """La consola de Windows suele ser cp1252: un emoji o una flecha en un print
    revienta con UnicodeEncodeError. Se fuerza utf-8 y, si la consola igual no
    puede dibujar un caracter, se muestra un '?' en vez de cortar la corrida."""
    for flujo in (sys.stdout, sys.stderr):
        try:
            flujo.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass


def main(argv=None):
    consola_utf8()
    ap = argparse.ArgumentParser(prog="inmobiliario",
                                 description="Buscador de oportunidades para renta en CABA")
    ap.add_argument("--base", help="ruta de la base SQLite (por defecto data/inmobiliario.db)")
    sub = ap.add_subparsers(dest="cmd", required=True)

    r = sub.add_parser("recolectar", help="recorrer los portales")
    r.add_argument("--limite", type=int, help="usar solo las N primeras busquedas del plan")
    r.add_argument("--solo-fuente", help="zonaprop | mercadolibre | argenprop")
    r.add_argument("--solo-barrio", help="probar con un solo barrio")
    r.add_argument("--geocodificar", action="store_true",
                   help="geocodificar todo durante la recoleccion (lento). "
                        "Por defecto se difiere al informe, que solo resuelve "
                        "las direcciones de los mejores candidatos.")

    sub.add_parser("informe", help="generar el informe HTML y dejar listo el ranking de la web")
    sub.add_parser("estado", help="resumen de la base")
    sub.add_parser("telegram", help="conectar el bot de Telegram (una sola vez)")

    a = sub.add_parser("avisar", help="mandar por Telegram lo nuevo que supere el umbral")
    a.add_argument("--umbral", type=float, help="puntaje minimo (por defecto, el de config)")
    a.add_argument("--simular", action="store_true",
                   help="mostrar en pantalla lo que se enviaria, sin mandar nada")

    sub.add_parser("escuchar", help="atender los botones de marcar")

    w = sub.add_parser("web", help="web local (solo en esta compu): listado, mapa y marcas")
    w.add_argument("--puerto", type=int, default=8765, help="puerto (por defecto 8765)")
    w.add_argument("--abrir", action="store_true", help="abrir el navegador al arrancar")

    d = sub.add_parser("desmarcar", help="borrar la marca de una propiedad")
    d.add_argument("clave", help="la clave de la propiedad, ej: zonaprop:123456")
    sub.add_parser("geocodificar", help="reintentar las direcciones sin coordenadas")

    args = ap.parse_args(argv)
    cfg = cargar()
    cfg["_base"] = Path(args.base).expanduser().resolve() if args.base \
        else cfg["_raiz"] / "data" / "inmobiliario.db"
    if args.cmd == "recolectar":
        recolectar(cfg, args)
    elif args.cmd == "informe":
        construir_informe(cfg, args)
    elif args.cmd == "geocodificar":
        regeocodificar(cfg, args)
    elif args.cmd == "telegram":
        from .telegram import configurar
        configurar(cfg, log)
    elif args.cmd == "avisar":
        avisar(cfg, args)
    elif args.cmd == "escuchar":
        escuchar(cfg, args)
    elif args.cmd == "web":
        from .web import servir
        servir(cfg, args.puerto, args.abrir, log)
    elif args.cmd == "desmarcar":
        desmarcar_una(cfg, args)
    else:
        estado(cfg, args)
    return 0


if __name__ == "__main__":
    sys.exit(main())
