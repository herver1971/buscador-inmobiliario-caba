"""MercadoLibre Inmuebles.

Nota sobre la API oficial: no sirve para esto. La busqueda y las fichas de
inmuebles de terceros devuelven 403 aun con OAuth completo (probado a fondo).
Por eso se lee el HTML publico, que expone los mismos datos en las tarjetas de
resultado y ademas trae la direccion con altura, que es lo que necesitamos
para geocodificar.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..config import barrio_canonico
from ..modelo import Publicacion
from .base import Fuente, Bloqueada, SinPresupuesto

BASE = "https://inmuebles.mercadolibre.com.ar"
TIPO_URL = {"depto": "departamentos", "ph": "ph", "casa": "casas"}
POR_PAGINA = 48


class MercadoLibre(Fuente):
    nombre = "mercadolibre"

    def url(self, tipo, operacion, barrio_slug, desde, precio_min=None, precio_max=None):
        """Arma la URL de busqueda.

        Dos rarezas de MercadoLibre que hay que respetar o no funciona nada:

        1. Sin `_NoIndex_True`, ML IGNORA tanto `_Desde` como `_OrderId`: te
           devuelve la primera pagina sin ordenar y sin ningun error.
        2. Filtrar por `_PriceRange_..USD` ademas de acotar el precio deja
           afuera las publicaciones en pesos, que ensucian el orden.

        Para VENTA ordenamos por precio ascendente: como cortamos a las N
        paginas, asi nos quedamos con las mas baratas de cada barrio, que es
        exactamente lo que buscamos.

        Para ALQUILER no ordenamos: las medianas necesitan una muestra
        representativa, y quedarnos con los alquileres mas baratos las
        deprimiria y sobreestimaria el rendimiento de todo.
        """
        partes = [BASE, TIPO_URL[tipo], operacion, "capital-federal", barrio_slug]
        url = "/".join(p for p in partes if p) + "/"
        modificadores = ""
        if operacion == "venta" and precio_max:
            modificadores += f"_PriceRange_{int(precio_min or 0)}USD-{int(precio_max)}USD"
            modificadores += "_OrderId_PRICE"
        if desde > 1:
            modificadores += f"_Desde_{desde}"
        if modificadores:
            url += modificadores + "_NoIndex_True"
        return url

    def recolectar(self, plan, al_encontrar=None):
        """plan: lista de (tipo, operacion, barrio, barrio_slug)."""
        salida = []
        base = self.cfg["precio"]
        por_tipo = base.get("por_tipo") or {}
        for tipo, operacion, barrio, bslug in plan:
            # El techo puede ser distinto por tipo: una casa no se consigue al
            # precio de un departamento.
            conf = {**base, **por_tipo.get(tipo, {})}
            precio_max = conf["max_recoleccion"]
            # El piso saca cocheras sueltas y errores de carga, que si no
            # ocupan las primeras paginas del orden ascendente.
            precio_min = conf["min_puntuable"]
            try:
                halladas = self._buscar(tipo, operacion, barrio, bslug,
                                        precio_min, precio_max)
                if al_encontrar is not None:
                    al_encontrar(halladas, tipo, operacion, barrio)
                else:
                    salida += halladas
            except SinPresupuesto as e:
                self.log(f"    {e}")
                self.reporte.detalle = str(e)
                break
            except Bloqueada:
                self.log(f"    fuente bloqueada, se corta MercadoLibre")
                break
        self.reporte.encontradas = len(salida)
        return salida

    def _buscar(self, tipo, operacion, barrio, bslug, precio_min, precio_max):
        encontradas, desde = [], 1
        for _ in range(self.tope_paginas(operacion)):
            venta = operacion == "venta"
            html = self.pedir(self.url(tipo, operacion, bslug, desde,
                                       precio_min if venta else None,
                                       precio_max if venta else None))
            if html is None:
                break
            tarjetas = self._parsear(html, tipo, operacion, barrio)
            if not tarjetas:
                break
            encontradas += tarjetas
            if len(tarjetas) < POR_PAGINA * 0.5:
                break                      # ultima pagina
            desde += POR_PAGINA
        return encontradas

    # ------------------------------------------------------------- parseo
    def _parsear(self, html, tipo, operacion, barrio):
        sopa = BeautifulSoup(html, "lxml")
        salida = []
        for c in sopa.select("div.poly-card"):
            try:
                p = self._tarjeta(c, tipo, operacion, barrio)
                if p:
                    salida.append(p)
            except Exception:
                continue        # una tarjeta rota no puede voltear la corrida
        return salida

    def _tarjeta(self, c, tipo, operacion, barrio):
        a = c.select_one("a.poly-component__title, h3.poly-component__title a, a[href*='mercadolibre.com.ar']")
        if not a or not a.get("href"):
            return None
        url = a["href"].split("#")[0]
        m = re.search(r"(MLA-?\d{6,})", url)
        if not m:
            return None
        id_fuente = m.group(1).replace("-", "")

        precio_el = c.select_one(".poly-price__current .andes-money-amount__fraction")
        precio = self.numero(precio_el.get_text() if precio_el else None)
        simbolo = c.select_one(".poly-price__current .andes-money-amount__currency-symbol")
        moneda = "USD" if simbolo and "U" in simbolo.get_text() else "ARS"

        # "Desde US$ ..." marca un emprendimiento: no es una unidad concreta.
        prefijo = c.select_one(".poly-price__prefix")
        headline = c.select_one(".poly-component__headline")
        emprendimiento = bool(prefijo) or (
            headline is not None and "EMPRENDIMIENTO" in headline.get_text().upper())

        ambientes = dormitorios = None
        m2c = m2t = None
        for it in c.select(".poly-attributes_list__item"):
            t = it.get_text(" ", strip=True).lower()
            n = self.numero(re.match(r"[\d.,]+", t).group()) if re.match(r"[\d.,]+", t) else None
            if n is None:
                continue
            if "amb" in t:
                ambientes = int(n)
            elif "dormitorio" in t:
                dormitorios = int(n)
            elif "m²" in t or "m2" in t:
                if "cubierto" in t:
                    m2c = n
                else:
                    m2t = n

        loc = c.select_one(".poly-component__location")
        direccion = loc.get_text(" ", strip=True) if loc else ""
        # "Sarmiento 4242, Almagro, Capital Federal" -> calle y barrio real
        partes = [x.strip() for x in direccion.split(",")]
        calle = partes[0] if partes else ""
        barrio_real = barrio_canonico(self.cfg, direccion) or barrio

        img = c.select_one(".poly-card__portada img, img.poly-component__picture, img")
        foto = (img.get("src") or img.get("data-src") or "") if img else ""
        # Las imagenes diferidas arrancan con un placeholder en base64.
        if foto.startswith("data:"):
            foto = (img.get("data-src") or "") if img else ""

        titulo_el = c.select_one(".poly-component__title")
        pub = Publicacion(
            fuente=self.nombre, id_fuente=id_fuente, url=url,
            operacion=operacion, tipo=tipo,
            titulo=titulo_el.get_text(" ", strip=True) if titulo_el else "",
            barrio=barrio_real or barrio, direccion=calle,
            precio=precio, moneda=moneda,
            m2_cubiertos=m2c, m2_totales=m2t,
            ambientes=ambientes, dormitorios=dormitorios,
            emprendimiento=emprendimiento, foto=foto,
        )
        return pub
