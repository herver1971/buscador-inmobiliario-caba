"""Geografia: el corte de Av. Rivadavia, geocodificacion y puntos de interes."""
from __future__ import annotations

import math
import re
import time
import unicodedata
from dataclasses import dataclass

import requests

# ---------------------------------------------------------------------------
# Poligonal de Av. Rivadavia, de Plaza de Mayo a General Paz.
#
# Precision estimada: +/- 150 m. No hace falta mas: la banda de duda
# configurable (geo.banda_duda_m, por defecto 300 m) absorbe el error, y ante
# la duda el sistema deja pasar la publicacion marcada en vez de descartarla.
# ---------------------------------------------------------------------------
RIVADAVIA = [
    (-34.6083, -58.3712),   # y Balcarce (Plaza de Mayo)
    (-34.6090, -58.3816),   # y 9 de Julio
    (-34.6094, -58.3925),   # y Callao (Congreso)
    (-34.6096, -58.4053),   # y Pueyrredon (Once)
    (-34.6120, -58.4200),   # y Medrano
    (-34.6188, -58.4370),   # y Acoyte (Caballito)
    (-34.6230, -58.4413),   # Primera Junta
    (-34.6270, -58.4520),   # y Boyaca
    (-34.6300, -58.4650),   # y Nazca (Flores)
    (-34.6325, -58.4770),   # y Segurola (Floresta)
    (-34.6355, -58.4900),   # y Lope de Vega
    (-34.6400, -58.5050),   # y Emilio Castro (Villa Luro)
    (-34.6435, -58.5220),   # y General Paz (Liniers)
]

# Caja de CABA, para descartar cualquier cosa de Provincia que se cuele.
CABA_BBOX = (-34.7100, -34.5200, -58.5400, -58.3300)  # lat_min, lat_max, lon_min, lon_max

# Barrios enteramente al norte de Rivadavia: no hace falta geocodificar.
BARRIOS_NORTE = {
    "palermo", "nunez", "belgrano", "colegiales", "villa urquiza", "villa crespo",
    "recoleta", "retiro", "almagro", "saavedra", "coghlan", "chacarita",
    "villa devoto", "villa del parque", "monte castro", "versalles",
    "villa pueyrredon", "villa ortuzar", "parque chas", "la paternal",
    "villa general mitre", "villa santa rita", "villa real", "agronomia",
    "puerto madero", "villa lugano norte",
}
# Barrios partidos por Rivadavia: hay que mirar la direccion.
BARRIOS_PARTIDOS = {
    "caballito", "flores", "floresta", "velez sarsfield", "villa luro",
    "liniers", "balvanera", "san nicolas", "monserrat",
}


def normalizar(texto: str) -> str:
    """Minusculas sin acentos, para comparar nombres de barrio."""
    if not texto:
        return ""
    t = unicodedata.normalize("NFKD", texto)
    t = "".join(c for c in t if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", t).strip().lower()


# "Av. Cabildo al 4300", "AV CABILDO 4300", "Cabildo 4358. Entre X e Y" son la
# misma direccion escrita por tres inmobiliarias distintas.
_TIPO_CALLE = r"^(av|avda|avenida|calle|pasaje|pje|psje|blvd|bv|boulevard|diagonal)\b\.?\s*"


def clave_direccion(direccion: str):
    """(calle, centena) para juntar los avisos de una misma direccion.

    La altura se redondea a la centena porque es lo que publica Zonaprop:
    "Del Signo 4000" es en realidad el 4058. O sea que esto agrupa por
    direccion tal como la publican, que en una cuadra corta es el edificio y
    en una cuadra larga puede ser el de al lado. Devuelve None si no hay
    altura: sin numero no se agrupa nada.
    """
    d = normalizar(direccion or "")
    # El tipo de calle se saca ANTES de cortar por el punto: "Av. Cabildo 4300"
    # cortado primero se queda en "av" y pierde la calle entera.
    d = re.sub(_TIPO_CALLE, "", d)
    d = re.split(r"[.,;]| entre | esq| e/ ", d)[0]
    d = d.replace(" al ", " ")
    m = re.search(r"(\d{1,5})\s*$", d.strip())
    if not m:
        return None
    altura = int(m.group(1))
    # "Comuna 11" no es una direccion: MercadoLibre la usa cuando el aviso no
    # dice donde queda. Sin este corte, 26 avisos de media Villa del Parque
    # quedaban agrupados como si fueran un edificio.
    if altura < 100:
        return None
    calle = re.sub(r"[^a-z0-9 ]", "", d[:m.start()]).strip()
    if not calle or calle in ("comuna", "barrio", "zona"):
        return None
    return (calle, altura // 100)


def slug(texto: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalizar(texto)).strip("-")


def haversine_m(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    r = 6371000.0
    p1, p2 = math.radians(lat1), math.radians(lat2)
    dp = p2 - p1
    dl = math.radians(lon2 - lon1)
    a = math.sin(dp / 2) ** 2 + math.cos(p1) * math.cos(p2) * math.sin(dl / 2) ** 2
    return 2 * r * math.asin(math.sqrt(a))


def _lat_de_rivadavia_en(lon: float) -> float:
    """Latitud de Av. Rivadavia a esa longitud, interpolando la poligonal."""
    pts = sorted(RIVADAVIA, key=lambda p: p[1])
    if lon <= pts[0][1]:
        return pts[0][0]
    if lon >= pts[-1][1]:
        return pts[-1][0]
    for (la1, lo1), (la2, lo2) in zip(pts, pts[1:]):
        if lo1 <= lon <= lo2:
            t = (lon - lo1) / (lo2 - lo1) if lo2 != lo1 else 0.0
            return la1 + t * (la2 - la1)
    return pts[-1][0]


def distancia_a_rivadavia_m(lat: float, lon: float) -> float:
    """Distancia perpendicular aproximada al eje de Rivadavia, en metros."""
    mejor = float("inf")
    for (la1, lo1), (la2, lo2) in zip(RIVADAVIA, RIVADAVIA[1:]):
        # Proyeccion local plana: suficiente en la escala de una ciudad.
        kx = 111320 * math.cos(math.radians(lat))
        ky = 110540
        px, py = (lon - lo1) * kx, (lat - la1) * ky
        vx, vy = (lo2 - lo1) * kx, (la2 - la1) * ky
        largo2 = vx * vx + vy * vy
        t = 0.0 if largo2 == 0 else max(0.0, min(1.0, (px * vx + py * vy) / largo2))
        dx, dy = px - t * vx, py - t * vy
        mejor = min(mejor, math.hypot(dx, dy))
    return mejor


@dataclass
class Ubicacion:
    """Resultado del recorte geografico."""
    dentro: bool
    confianza: str        # "exacta" | "aproximada" | "barrio" | "desconocida"
    motivo: str
    dudosa: bool = False  # entra, pero conviene mirar el mapa

    @property
    def marca(self) -> str:
        if not self.dudosa:
            return ""
        return "verificar ubicacion"


def en_zona(lat, lon, barrio, cfg) -> Ubicacion:
    """Decide si una propiedad esta en la zona buscada (al norte de Rivadavia).

    Regla de oro: nunca se descarta por duda. Solo se descarta lo que esta
    inequivocamente al sur, o fuera de CABA.
    """
    g = cfg["geo"]
    barrio_n = normalizar(barrio or "")

    if lat is not None and lon is not None:
        lat_min, lat_max, lon_min, lon_max = CABA_BBOX
        if not (lat_min <= lat <= lat_max and lon_min <= lon <= lon_max):
            return Ubicacion(False, "exacta", "fuera de CABA")

        dist = distancia_a_rivadavia_m(lat, lon)
        al_norte = lat > _lat_de_rivadavia_en(lon)

        if al_norte:
            return Ubicacion(True, "exacta", f"al norte de Rivadavia ({dist:.0f} m)",
                             dudosa=dist < g["banda_duda_m"])

        # Al sur: solo sobrevive la excepcion de Caballito.
        exc = g.get("excepcion_caballito", {})
        if exc.get("activa") and barrio_n == "caballito" and dist <= exc["metros_al_sur"]:
            return Ubicacion(True, "exacta",
                             f"excepcion Caballito ({dist:.0f} m al sur)", dudosa=True)
        if dist < g["banda_duda_m"]:
            return Ubicacion(True, "exacta",
                             f"al sur pero a {dist:.0f} m del eje: pasa por las dudas",
                             dudosa=True)
        return Ubicacion(False, "exacta", f"al sur de Rivadavia ({dist:.0f} m)")

    # Sin coordenadas: se resuelve por barrio.
    if not g.get("aceptar_sin_geocodificar", True):
        return Ubicacion(False, "desconocida", "sin coordenadas")
    if barrio_n in BARRIOS_NORTE:
        return Ubicacion(True, "barrio", "barrio entero al norte de Rivadavia")
    if barrio_n in BARRIOS_PARTIDOS:
        return Ubicacion(True, "barrio", "barrio partido por Rivadavia", dudosa=True)
    if barrio_n:
        return Ubicacion(False, "barrio", f"barrio fuera de la zona: {barrio}")
    return Ubicacion(True, "desconocida", "sin barrio ni coordenadas", dudosa=True)


# ---------------------------------------------------------------------------
# Geocodificacion con el normalizador oficial de la Ciudad (USIG).
# Gratis, sin clave, y devuelve coordenadas a partir de calle + altura.
# ---------------------------------------------------------------------------
USIG = "https://servicios.usig.buenosaires.gob.ar/normalizar/"


class Geocodificador:
    """Geocodifica direcciones de CABA, cacheando en la base para no repetir."""

    def __init__(self, db, timeout=15):
        self.db = db
        self.timeout = timeout
        self.sesion = requests.Session()
        self.consultas = 0

    def __call__(self, direccion: str):
        """Devuelve (lat, lon) o (None, None). Nunca lanza."""
        if not direccion:
            return None, None
        clave = normalizar(direccion)
        hit = self.db.geocache_leer(clave)
        if hit is not None:
            return hit

        calle_altura = self._limpiar(direccion)
        if not calle_altura:
            self.db.geocache_guardar(clave, None, None)
            return None, None
        try:
            self.consultas += 1
            if self.consultas % 20 == 0:
                time.sleep(1.0)     # USIG corta la conexion si se le insiste
            r = self.sesion.get(USIG, params={"direccion": calle_altura,
                                              "geocodificar": "true"},
                                timeout=self.timeout)
            datos = r.json().get("direccionesNormalizadas") or []
            if datos and datos[0].get("coordenadas"):
                c = datos[0]["coordenadas"]
                lat, lon = float(c["y"]), float(c["x"])
                self.db.geocache_guardar(clave, lat, lon)
                return lat, lon
        except Exception:
            return None, None   # no se cachea el fallo de red, se reintenta manana
        self.db.geocache_guardar(clave, None, None)
        return None, None

    @staticmethod
    def _limpiar(direccion: str) -> str:
        """'Emilio Mitre 400, Piso 16', Caballito' -> 'Emilio Mitre 400'.

        Ojo con el 'Al' de altura: MercadoLibre publica muchisimas direcciones
        como 'Bauness Al 2500'. USIG rechaza esa forma y devuelve nada, asi que
        hay que sacarlo. Es la causa del 78% de las fallas de geocodificacion.
        """
        d = direccion.split(",")[0]
        d = re.sub(r"\b(piso|depto|dpto|uf|unidad|of|oficina)\b.*", "", d, flags=re.I)
        # 'Bauness Al 2500' -> 'Bauness 2500'; tambien 'al 2500' y 'Alt. 2500'
        d = re.sub(r"\s+al(?:t\.?|tura)?\s+(?=\d)", " ", d, flags=re.I)
        d = re.sub(r"\bs/?n\b", "", d, flags=re.I)
        d = re.sub(r"\s+", " ", d).strip(" .-")
        # Necesita altura numerica: sin altura USIG no puede ubicar el punto.
        return d if re.search(r"\d{1,5}\s*$", d) else ""


class PuntosDeInteres:
    """Calcula el bonus de cercania a universidades y hospitales."""

    def __init__(self, pois, geocodificador, bonus_max: float):
        self.bonus_max = bonus_max
        self.pois = []
        for p in pois:
            lat, lon = p.get("lat"), p.get("lon")
            if lat is None and p.get("direccion"):
                lat, lon = geocodificador(p["direccion"])
            if lat is None:
                continue
            self.pois.append({**p, "lat": lat, "lon": lon})

    @property
    def resueltos(self) -> int:
        return len(self.pois)

    def bonus(self, lat, lon):
        """Devuelve (bonus_saturado, [nombres de los POIs que aportaron])."""
        if lat is None or lon is None:
            return 0.0, []
        total, cerca = 0.0, []
        for p in self.pois:
            d = haversine_m(lat, lon, p["lat"], p["lon"])
            if d <= p["radio_m"]:
                # Decae linealmente del centro al borde del radio.
                aporte = p["bonus"] * (1 - d / p["radio_m"])
                if aporte > 0.05:
                    total += aporte
                    cerca.append((p["nombre"], int(d), round(aporte, 2)))
        cerca.sort(key=lambda x: -x[2])
        return min(total, self.bonus_max), cerca
