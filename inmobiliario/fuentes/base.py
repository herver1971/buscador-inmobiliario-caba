"""Infraestructura comun a todas las fuentes.

Dos ideas de diseno importantes:

1. Cada fuente es un plugin independiente. Si a una la bloquean o le cambian
   el HTML, las demas siguen andando y el informe avisa que esa quedo ciega.

2. El cuello de botella real NO es el parseo sino el ritmo de pedidos. Los
   portales estan detras de WAFs que bloquean por IP con muy poca provocacion
   (Argenprop corta a los ~10 pedidos rapidos). Por eso el ritmo es lento y
   configurable, y ante un bloqueo la fuente se rinde en vez de insistir.
"""
from __future__ import annotations

import random
import time
from dataclasses import dataclass, field

import requests

UA = ("Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0.0.0 Safari/537.36")

CABECERAS = {
    "User-Agent": UA,
    "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
    "Accept-Language": "es-AR,es;q=0.9,en;q=0.8",
    "Upgrade-Insecure-Requests": "1",
    "Sec-Fetch-Dest": "document",
    "Sec-Fetch-Mode": "navigate",
    "Sec-Fetch-Site": "none",
    "Connection": "keep-alive",
}


class Bloqueada(Exception):
    """El portal nos esta rechazando: no tiene sentido seguir insistiendo."""


class SinPresupuesto(Exception):
    """Se agoto el tope de pedidos de la corrida. Se corta ordenadamente."""


@dataclass
class Reporte:
    """Que le paso a esta fuente en la corrida. Va al informe."""
    nombre: str
    paginas: int = 0
    encontradas: int = 0
    errores: int = 0
    ciega: bool = False
    detalle: str = ""
    urls_fallidas: list = field(default_factory=list)


class Fuente:
    nombre = "base"

    def __init__(self, cfg, log=print):
        self.cfg = cfg
        self.log = log
        c = cfg["fuentes"][self.nombre]
        self.paginas_max = c.get("paginas_max", 5)
        self.paginas_max_alquiler = c.get("paginas_max_alquiler", self.paginas_max)
        self.espera = c.get("espera_seg", [4, 9])
        self.timeout = cfg["recoleccion"]["timeout_seg"]
        self.max_errores = cfg["recoleccion"]["errores_para_abortar"]
        self.presupuesto = cfg["recoleccion"].get("pedidos_max_por_corrida", 10**6)
        self.sesion = requests.Session()
        self.sesion.headers.update(CABECERAS)
        self.reporte = Reporte(self.nombre)
        self._errores_seguidos = 0
        self._primer_pedido = True

    def tope_paginas(self, operacion: str) -> int:
        """El alquiler solo alimenta medianas: con pocas paginas por barrio alcanza."""
        return self.paginas_max_alquiler if operacion == "alquiler" else self.paginas_max

    # ------------------------------------------------------------------ red
    def pedir(self, url: str) -> str | None:
        """Un GET con ritmo humano. Devuelve el HTML o None."""
        if self.presupuesto <= 0:
            raise SinPresupuesto(
                "se alcanzo el tope de pedidos de la corrida "
                "(recoleccion.pedidos_max_por_corrida)")
        self.presupuesto -= 1
        if not self._primer_pedido:
            time.sleep(random.uniform(*self.espera))
        self._primer_pedido = False
        try:
            r = self.sesion.get(url, timeout=self.timeout)
        except Exception as e:
            self._fallo(url, f"red: {e}")
            return None

        # 202 con cuerpo vacio y 403 son las firmas tipicas de un WAF.
        if r.status_code in (202, 403, 429) or len(r.content) < 5000:
            self._fallo(url, f"HTTP {r.status_code}, {len(r.content)} bytes "
                             f"(parece bloqueo del portal)")
            return None
        if r.status_code != 200:
            self._fallo(url, f"HTTP {r.status_code}")
            return None

        self._errores_seguidos = 0
        self.reporte.paginas += 1
        return r.text

    def _fallo(self, url, detalle):
        self._errores_seguidos += 1
        self.reporte.errores += 1
        self.reporte.urls_fallidas.append(url)
        self.log(f"    ! {detalle}")
        if self._errores_seguidos >= self.max_errores:
            self.reporte.ciega = True
            self.reporte.detalle = detalle
            raise Bloqueada(detalle)

    # --------------------------------------------------------------- helpers
    @staticmethod
    def numero(texto, entero=False):
        """'USD 54.000' -> 54000.0 ; '28 m2' -> 28.0"""
        import re
        if not texto:
            return None
        t = re.sub(r"[^\d,.]", "", str(texto))
        if not t:
            return None
        # Formato argentino: el punto es separador de miles.
        t = t.replace(".", "").replace(",", ".")
        try:
            v = float(t)
        except ValueError:
            return None
        return int(v) if entero else v

    def recolectar(self, plan, al_encontrar=None) -> list:
        """Recorre el plan.

        `al_encontrar(publicaciones, tipo, operacion, barrio)` se llama despues
        de cada busqueda. Sirve para ir guardando sobre la marcha en vez de
        acumular todo en memoria y guardar al final: una corrida larga que se
        corta no puede perder horas de trabajo, y ademas permite ver el avance.
        """
        raise NotImplementedError
