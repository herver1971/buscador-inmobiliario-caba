"""Zonaprop: la mejor fuente de las tres.

Cada pagina de resultados trae un `window.__PRELOADED_STATE__` con un JSON por
aviso que incluye **latitud y longitud exactas**, direccion con altura, expensas,
antiguedad, superficie total y cubierta, ambientes, dormitorios y precio con
moneda. Es, en la practica, la API que MercadoLibre nos nego.

Que las coordenadas vengan servidas elimina toda la geocodificacion: el corte de
Rivadavia y las distancias a hospitales y facultades pasan a ser dato duro.

Dos rarezas de sus URLs que costaron descubrir:

1. El sufijo es `-dolar`, NO `-dolares`. Con `-dolares` Zonaprop descarta el
   barrio y te devuelve el listado de toda Argentina, sin avisar.
2. `-orden-precio-ascendente` funciona pero pone primero los avisos de
   "consultar precio" (importe 0), que ocupan paginas enteras. Por eso no se
   ordena: se recorre por BANDAS de precio (`50000-70000-dolar`), que ademas
   acota el universo a algo que se puede barrer de verdad.
"""
from __future__ import annotations

import json

from ..config import barrio_canonico
from ..modelo import Publicacion
from .base import Fuente, Bloqueada, SinPresupuesto

BASE = "https://www.zonaprop.com.ar"
TIPO_URL = {"depto": "departamentos", "ph": "ph", "casa": "casas"}

# Identificadores de mainFeatures. Son estables y estan documentados de hecho
# en el propio JSON (cada feature trae su `label`).
AMBIENTES, DORMITORIOS, BANOS = "CFT1", "CFT2", "CFT3"
SUP_TOTAL, SUP_CUBIERTA = "CFT100", "CFT101"
ANTIGUEDAD, COCHERA = "CFT5", "CFT7"


class Zonaprop(Fuente):
    nombre = "zonaprop"

    def bandas(self):
        """Cortes de precio para barrer el rango sin paginar a ciegas."""
        p = self.cfg["precio"]
        cortes = self.cfg["fuentes"]["zonaprop"].get("bandas_usd") or [
            p["min_puntuable"], p["ideal_min"], p["ideal_max"],
            p["cae_desde"], p["max_recoleccion"]]
        cortes = sorted(set(int(c) for c in cortes))
        return list(zip(cortes, cortes[1:]))

    def url(self, tipo, operacion, barrio_slug, banda=None, pagina=1):
        partes = [TIPO_URL[tipo], operacion, barrio_slug]
        if banda:
            partes.append(f"{banda[0]}-{banda[1]}-dolar")
        if pagina > 1:
            partes.append(f"pagina-{pagina}")
        return f"{BASE}/{'-'.join(partes)}.html"

    def recolectar(self, plan, al_encontrar=None):
        salida = []
        for tipo, operacion, barrio, bslug in plan:
            try:
                halladas = self._buscar(tipo, operacion, barrio, bslug)
                if al_encontrar is not None:
                    al_encontrar(halladas, tipo, operacion, barrio)
                else:
                    salida += halladas
            except SinPresupuesto as e:
                self.log(f"    {e}")
                self.reporte.detalle = str(e)
                break
            except Bloqueada:
                self.log("    fuente bloqueada, se corta Zonaprop")
                break
        self.reporte.encontradas = len(salida)
        return salida

    def _buscar(self, tipo, operacion, barrio, bslug):
        # En alquiler los precios estan en pesos, asi que la banda en dolares
        # no aplica: se recorre el listado tal cual.
        bandas = self.bandas() if operacion == "venta" else [None]
        encontradas = []
        for banda in bandas:
            for pagina in range(1, self.tope_paginas(operacion) + 1):
                html = self.pedir(self.url(tipo, operacion, bslug, banda, pagina))
                if html is None:
                    break
                avisos = self._parsear(html, tipo, operacion, barrio)
                if not avisos:
                    break
                encontradas += avisos
                if len(avisos) < 25:        # ultima pagina de la banda
                    break
        return encontradas

    # ------------------------------------------------------------- parseo
    @staticmethod
    def _estado_precargado(html):
        """Extrae el JSON embebido respetando las llaves balanceadas.

        Un regex greedy se pasa de largo hasta el siguiente </script> y rompe
        el parseo; raw_decode corta exactamente donde termina el objeto.
        """
        marca = "window.__PRELOADED_STATE__"
        if marca not in html:
            return None
        inicio = html.index("{", html.index(marca))
        datos, _ = json.JSONDecoder().raw_decode(html[inicio:])
        return datos

    def _parsear(self, html, tipo, operacion, barrio):
        try:
            estado = self._estado_precargado(html)
            avisos = (estado or {}).get("listStore", {}).get("listPostings") or []
        except Exception:
            return []
        salida = []
        for a in avisos:
            try:
                p = self._aviso(a, tipo, operacion, barrio)
                if p:
                    salida.append(p)
            except Exception:
                continue
        return salida

    def _aviso(self, a, tipo, operacion, barrio):
        pid = a.get("postingId")
        if not pid:
            return None

        # El precio tiene que ser el de LA operacion que se esta recolectando.
        # Un aviso de "venta y alquiler" trae las dos, y quedarse con la primera
        # guardaba el alquiler como si fuera el precio de venta —o peor, el
        # precio de venta como si fuera un alquiler, que envenena las medianas
        # de las que sale el rendimiento.
        precio = moneda = None
        for op in a.get("priceOperationTypes") or []:
            nombre = ((op.get("operationType") or {}).get("name") or "").lower()
            # Sin nombre no se puede decidir: se acepta, que es como venia
            # funcionando. "Alquiler temporal" no es "alquiler": el precio de
            # una noche o de una semana no compara con un contrato.
            if nombre and nombre != operacion:
                continue
            for pr in op.get("prices") or []:
                if pr.get("amount"):
                    precio, moneda = pr["amount"], pr.get("currency") or "USD"
                    break
            if precio:
                break
        if not precio:
            # "Consultar precio", o un aviso que solo publica precio para la
            # otra operacion: en los dos casos no hay nada que puntuar.
            return None

        loc = a.get("postingLocation") or {}
        direccion = ((loc.get("address") or {}).get("name") or "").strip()
        geoloc = ((loc.get("postingGeolocation") or {}).get("geolocation")) or {}
        barrio_json = ((loc.get("location") or {}).get("name")) or ""

        # Portada del aviso. Zonaprop ofrece varios tamanos; 730x532 es el que
        # mejor se ve en Telegram sin ser innecesariamente pesado.
        fotos = ((a.get("visiblePictures") or {}).get("pictures")) or []
        foto = (fotos[0].get("url730x532") or fotos[0].get("url360x266")) if fotos else ""

        f = a.get("mainFeatures") or {}
        val = lambda k: self.numero((f.get(k) or {}).get("value"))

        expensas = None
        exp = a.get("expenses") or {}
        if exp.get("amount") and exp.get("currency") in ("$", "ARS", None):
            expensas = float(exp["amount"]) or None

        return Publicacion(
            fuente=self.nombre, id_fuente=str(pid),
            url=BASE + (a.get("url") or ""),
            operacion=operacion, tipo=tipo,
            titulo=(a.get("title") or a.get("generatedTitle") or "").strip(),
            barrio=barrio_canonico(self.cfg, barrio_json) or barrio,
            direccion=direccion,
            precio=float(precio),
            moneda="USD" if moneda in ("USD", "U$S") else "ARS",
            expensas_ars=expensas,
            m2_cubiertos=val(SUP_CUBIERTA), m2_totales=val(SUP_TOTAL),
            ambientes=int(val(AMBIENTES)) if val(AMBIENTES) else None,
            dormitorios=int(val(DORMITORIOS)) if val(DORMITORIOS) else None,
            antiguedad=int(val(ANTIGUEDAD)) if val(ANTIGUEDAD) is not None else None,
            # La URL es la senal mas confiable: un emprendimiento publica
            # MUCHAS unidades bajo un solo aviso, y Zonaprop muestra la que
            # entra en la banda de precio que pediste. Comparar ese precio
            # entre corridas produce "bajadas" que no existen: son unidades
            # distintas del mismo edificio.
            emprendimiento=bool(a.get("developmentFeatures") or a.get("units")
                                or "/emprendimiento/" in (a.get("url") or "")),
            descripcion=(a.get("descriptionNormalized") or "")[:400],
            foto=foto or "",
            # Lo mas valioso: no hay que geocodificar nada.
            lat=geoloc.get("latitude"), lon=geoloc.get("longitude"),
            extra={"cochera": bool(val(COCHERA)),
                   "publisher": (a.get("publisher") or {}).get("name")},
        )
