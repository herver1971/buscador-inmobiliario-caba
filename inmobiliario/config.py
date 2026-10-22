"""Carga de configuracion, cotizacion del dolar y canonicalizacion de barrios."""
from __future__ import annotations

from pathlib import Path

import requests
import yaml

from .geo import normalizar, slug

RAIZ = Path(__file__).resolve().parent.parent

# Sub-barrios y nombres comerciales que los portales usan, mapeados al barrio
# canonico de barrios.yaml. Sin esto, "Palermo Hollywood" no matchea con nada.
ALIAS = {
    "palermo hollywood": "Palermo", "palermo soho": "Palermo",
    "palermo chico": "Palermo", "palermo nuevo": "Palermo",
    "palermo viejo": "Palermo", "palermo botanico": "Palermo",
    "las canitas": "Palermo", "alto palermo": "Palermo",
    "belgrano c": "Belgrano", "belgrano r": "Belgrano",
    "belgrano chico": "Belgrano", "barrancas de belgrano": "Belgrano",
    "bajo belgrano": "Belgrano",
    "barrio norte": "Recoleta", "recoleta / barrio norte": "Recoleta",
    "once": "Balvanera", "congreso": "Balvanera", "abasto": "Balvanera",
    "tribunales": "San Nicolas", "centro": "San Nicolas",
    "microcentro": "San Nicolas", "san nicolas / centro": "San Nicolas",
    "catalinas": "Retiro",
    "caballito norte": "Caballito", "caballito sur": "Caballito",
    "primera junta": "Caballito", "parque centenario": "Caballito",
    "villa mitre": "Villa General Mitre",
    "parque rivadavia": "Caballito",
    "distrito quartier": "Retiro",
    "nunez": "Nunez", "nuñez": "Nunez",
    "chacarita / villa crespo": "Chacarita",
    "villa pueyrredon": "Villa Pueyrredon",
    "agronomia": "Agronomia",
}


def cargar(raiz: Path = RAIZ) -> dict:
    cfg = yaml.safe_load((raiz / "config" / "config.yaml").read_text(encoding="utf-8"))
    cfg["barrios"] = yaml.safe_load(
        (raiz / "config" / "barrios.yaml").read_text(encoding="utf-8"))["barrios"]
    cfg["pois"] = yaml.safe_load(
        (raiz / "config" / "pois.yaml").read_text(encoding="utf-8"))["pois"]
    cfg["exclusiones"] = yaml.safe_load(
        (raiz / "config" / "exclusiones.yaml").read_text(encoding="utf-8"))["exclusiones"]
    cfg["_raiz"] = raiz

    # Indice normalizado para resolver el barrio que informa cada portal.
    cfg["_barrios_norm"] = {normalizar(b): b for b in cfg["barrios"]}
    return cfg


def barrio_canonico(cfg, texto: str) -> str | None:
    """'Palermo Hollywood, Capital Federal' -> 'Palermo'. None si no es de la zona."""
    if not texto:
        return None
    for parte in [p.strip() for p in texto.split(",")]:
        n = normalizar(parte)
        if not n or n in ("capital federal", "caba", "buenos aires"):
            continue
        if n in cfg["_barrios_norm"]:
            return cfg["_barrios_norm"][n]
        if n in ALIAS:
            return ALIAS[n]
        # "Villa Urquiza Norte" -> el barrio canonico mas largo que sea prefijo
        candidatos = [c for cn, c in cfg["_barrios_norm"].items() if n.startswith(cn)]
        if candidatos:
            return max(candidatos, key=len)
    return None


def plan_de_busqueda(cfg) -> list:
    """Que combinaciones (tipo, operacion, barrio) vale la pena pedir.

    Solo barrios con peso > 0 para ese tipo. Alquiler se releva para todos los
    barrios que interesen en venta: alimenta las medianas de rendimiento.
    """
    plan = []
    for barrio, pesos in cfg["barrios"].items():
        for tipo in cfg["busqueda"]["tipos"]:
            peso = pesos.get(tipo, 0)
            if peso <= 0:
                continue
            for operacion in cfg["busqueda"]["operaciones"]:
                plan.append((tipo, operacion, barrio, slug(barrio), peso))
    # Venta antes que alquiler, y barrios de mas peso primero: si la corrida se
    # corta por tope o por bloqueo, se corta por lo que menos importa.
    plan.sort(key=lambda x: (x[1] != "venta", -x[4]))
    return [(t, o, b, s) for t, o, b, s, _ in plan]


def cotizacion(cfg, log=print) -> float:
    """USD/ARS para poder comparar alquileres en pesos con precios en dolares."""
    casa = cfg.get("cotizacion", {}).get("casa", "bolsa")
    respaldo = cfg.get("cotizacion", {}).get("respaldo", 1540.0)
    try:
        r = requests.get(f"https://dolarapi.com/v1/dolares/{casa}", timeout=10)
        v = float(r.json()["venta"])
        log(f"  dolar {casa}: ${v:,.0f}")
        return v
    except Exception:
        log(f"  ! no se pudo obtener el dolar, se usa el respaldo ${respaldo:,.0f}")
        return respaldo
