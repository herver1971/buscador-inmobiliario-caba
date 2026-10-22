"""Argenprop.

Es la fuente mas rica: cada aviso trae los datos estructurados como atributos
del <a class="card"> (monto, moneda, dormitorios, tipo, barrio) y ademas la
direccion CON ALTURA, que es lo que permite geocodificar y aplicar el corte
de Rivadavia con precision.

Contrapartida: esta detras de CloudFront con bot control y bloquea por IP con
muy poca provocacion (se corta a los ~10 pedidos rapidos y el bloqueo dura un
buen rato). Por eso la espera por defecto es de 12 a 25 segundos. Si igual te
bloquea, subila; no hay apuro, esto corre de noche.
"""
from __future__ import annotations

import re

from bs4 import BeautifulSoup

from ..config import barrio_canonico
from ..modelo import Publicacion
from .base import Fuente, Bloqueada, SinPresupuesto

BASE = "https://www.argenprop.com"
TIPO_URL = {"depto": "departamentos", "ph": "ph", "casa": "casas"}


class Argenprop(Fuente):
    nombre = "argenprop"

    def url(self, tipo, operacion, barrio_slug, pagina):
        u = f"{BASE}/{TIPO_URL[tipo]}/{operacion}/{barrio_slug}"
        return u if pagina <= 1 else f"{u}-pagina-{pagina}"

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
                self.log("    fuente bloqueada, se corta Argenprop")
                break
        self.reporte.encontradas = len(salida)
        return salida

    def _buscar(self, tipo, operacion, barrio, bslug):
        encontradas = []
        for pagina in range(1, self.tope_paginas(operacion) + 1):
            html = self.pedir(self.url(tipo, operacion, bslug, pagina))
            if html is None:
                break
            tarjetas = self._parsear(html, tipo, operacion, barrio)
            if not tarjetas:
                break
            encontradas += tarjetas
            if len(tarjetas) < 15:
                break
        return encontradas

    def _parsear(self, html, tipo, operacion, barrio):
        sopa = BeautifulSoup(html, "lxml")
        salida = []
        for c in sopa.select("a.card[data-item-card]"):
            try:
                p = self._tarjeta(c, tipo, operacion, barrio)
                if p:
                    salida.append(p)
            except Exception:
                continue
        return salida

    def _tarjeta(self, c, tipo, operacion, barrio):
        id_fuente = c.get("data-item-card") or c.get("idaviso")
        if not id_fuente:
            return None

        texto_precio = self._texto(c, ".card__price")
        moneda = "USD" if re.search(r"USD|U\$S|u\$s", texto_precio) else "ARS"
        precio = self.numero(c.get("montonormalizado")) or self.numero(
            re.sub(r"\+.*", "", texto_precio))

        expensas = None
        exp = self._texto(c, ".card__expenses")
        if exp:
            expensas = self.numero(exp)

        # "28 m² cubie. 1 dorm. 45 años"
        m2 = ambientes = dormitorios = antiguedad = None
        for it in c.select(".card__main-features li, .card__main-features span"):
            t = it.get_text(" ", strip=True).lower()
            n = self.numero(t)
            if n is None:
                continue
            if "m²" in t or "m2" in t:
                m2 = m2 or n
            elif "dorm" in t:
                dormitorios = int(n)
            elif "amb" in t:
                ambientes = int(n)
            elif "año" in t or "anos" in t:
                antiguedad = int(n)
        if m2 is None:
            crudo = self._texto(c, ".card__main-features")
            mm = re.search(r"([\d.,]+)\s*m", crudo)
            if mm:
                m2 = self.numero(mm.group(1))
        dormitorios = dormitorios or self.numero(c.get("dormitorios"), entero=True)
        ambientes = ambientes or self.numero(c.get("ambientes"), entero=True)
        if ambientes is None and dormitorios is not None:
            ambientes = dormitorios + 1

        direccion = self._texto(c, ".card__address")
        return Publicacion(
            fuente=self.nombre, id_fuente=str(id_fuente),
            url=BASE + (c.get("href") or ""),
            operacion=operacion, tipo=tipo,
            titulo=self._texto(c, ".card__title"),
            barrio=barrio_canonico(self.cfg, f"{direccion}, {barrio}") or barrio,
            direccion=direccion.split(",")[0].strip(),
            precio=precio, moneda=moneda, expensas_ars=expensas,
            m2_cubiertos=m2, ambientes=ambientes, dormitorios=dormitorios,
            antiguedad=antiguedad,
            descripcion=self._texto(c, ".card__info")[:400],
            foto=(lambda i: (i.get("src") or "") if i else "")(
                c.select_one(".card__photos img")),
        )

    @staticmethod
    def _texto(nodo, sel):
        el = nodo.select_one(sel)
        return re.sub(r"\s+", " ", el.get_text(" ", strip=True)) if el else ""
