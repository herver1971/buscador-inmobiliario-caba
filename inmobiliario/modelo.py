"""Formato comun al que cada portal normaliza sus publicaciones."""
from __future__ import annotations

import hashlib
import re
from dataclasses import dataclass, field, asdict
from datetime import date


@dataclass
class Publicacion:
    fuente: str
    id_fuente: str
    url: str
    operacion: str                  # venta | alquiler
    tipo: str                       # depto | ph | casa
    titulo: str = ""
    barrio: str = ""
    direccion: str = ""
    precio: float | None = None     # en la moneda original
    moneda: str = "USD"
    precio_usd: float | None = None
    expensas_ars: float | None = None
    m2_cubiertos: float | None = None
    m2_totales: float | None = None
    ambientes: int | None = None
    dormitorios: int | None = None
    antiguedad: int | None = None
    emprendimiento: bool = False    # obra nueva multi-unidad: no es una unidad concreta
    descripcion: str = ""
    foto: str = ""          # URL de la portada, para el aviso de Telegram y la web
    lat: float | None = None
    lon: float | None = None
    extra: dict = field(default_factory=dict)

    @property
    def clave(self) -> str:
        return f"{self.fuente}:{self.id_fuente}"

    # Limites de cordura por tipo. Los portales publican metrajes absurdos
    # (se vieron 5.564 m2 en un 3 ambientes) y un m2 mal cargado envenena
    # tanto el USD/m2 como la estimacion de alquiler.
    LIMITES_M2 = {"depto": (12, 500), "ph": (15, 600), "casa": (25, 2000)}

    @property
    def m2(self) -> float | None:
        minimo, maximo = self.LIMITES_M2.get(self.tipo, (10, 2000))
        for v in (self.m2_cubiertos, self.m2_totales):
            if v and minimo <= v <= maximo:
                return v
        return None

    @property
    def precio_m2(self) -> float | None:
        if self.precio_usd and self.m2 and self.m2 > 5:
            return self.precio_usd / self.m2
        return None

    @property
    def huella(self) -> str:
        """Huella para deduplicar la misma propiedad publicada en varios lados.

        Direccion normalizada + superficie redondeada + ambientes. Es
        deliberadamente conservadora: preferimos duplicar a fusionar mal.
        """
        dir_n = re.sub(r"[^a-z0-9 ]", "", (self.direccion or "").lower())
        dir_n = re.sub(r"\s+", " ", dir_n).strip()
        m2 = int(round((self.m2 or 0) / 5.0) * 5)
        base = f"{dir_n}|{m2}|{self.ambientes or 0}|{self.operacion}"
        if not dir_n:
            base = f"{self.clave}"      # sin direccion no se arriesga a fusionar
        return hashlib.sha1(base.encode()).hexdigest()[:16]

    def dict(self) -> dict:
        d = asdict(self)
        d.pop("extra", None)
        return d
