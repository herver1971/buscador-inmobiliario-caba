"""Puntaje de una publicacion como inversion para renta.

    peso_efectivo = min(10, peso_barrio_tipo + bonus_pois)
    factor_zona   = (peso_efectivo / 10) ** exponente_zona
    puntaje       = merito * factor_zona

'merito' (0..100) mira la propiedad; 'factor_zona' mira donde esta. Los puntos
de interes entran del lado de la zona, que es lo que permite que las cinco
cuadras alrededor del Hospital Italiano puntuen como un barrio mejor.

Cada componente devuelve 0..1 y se multiplica por su peso de config.yaml:
rendimiento 30, precio 20, precio_m2 15, señales 15, ambientes 10, expensas 10.
El README los explica uno por uno con los cortes y con lo que dan medidos sobre
la base real, en la seccion "Como puntua".

Casi nada se descarta: todo vive en el puntaje. Ampliar la busqueda es bajar
un umbral en el informe, no tocar este archivo. Lo poco que si desaparece esta
todo junto en filtro_duro().
"""
from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from datetime import date

from .db import banda_m2
from .geo import normalizar


def _clamp(x, a=0.0, b=1.0):
    return max(a, min(b, x))


def _n(v, decimales=0):
    """Numero con separador de miles argentino: 1812 -> '1.812'."""
    return f"{v:,.{decimales}f}".replace(",", "@").replace(".", ",").replace("@", ".")


@dataclass
class Resultado:
    puntaje: float = 0.0
    merito: float = 0.0
    peso_zona: float = 0.0
    peso_efectivo: float = 0.0
    factor_zona: float = 0.0
    rendimiento: float | None = None          # bruto anual, ej 0.078
    rendimiento_temporario: float | None = None
    alquiler_estimado: float | None = None
    componentes: dict = field(default_factory=dict)
    motivos: list = field(default_factory=list)
    pois_cerca: list = field(default_factory=list)
    descartada: str | None = None


class Evaluador:
    def __init__(self, cfg, medianas_alquiler, medianas_m2, pois, cotizacion,
                 alquiler_m2=None, expensas_m2=None):
        self.cfg = cfg
        self.s = cfg["scoring"]
        self.p = cfg["precio"]
        # medianas_alquiler es (vacio, equipado): un depto amoblado alquila
        # ~30% mas caro, y mezclarlos sobreestima el rendimiento de todo.
        self.alq, self.alq_equipado = medianas_alquiler
        self.m2 = medianas_m2
        self.alq_m2 = alquiler_m2 or {}
        # Referencia global de alquiler por m2, para escalar cuando no hay dato
        # del barrio. Se pondera despues por el nivel de precios de la zona.
        self._alq_m2_global = (statistics.median(list(self.alq_m2.values()))
                               if self.alq_m2 else None)
        self._venta_m2_global = (statistics.median(list(medianas_m2.values()))
                                 if medianas_m2 else None)
        # Cuatro niveles de referencia de expensas por m2, del mas especifico
        # al mas general. Vacios si todavia no hay muestra: el componente se
        # apaga solo y las expensas se puntuan solo por su carga sobre la renta.
        self.exp_m2 = expensas_m2 or ({}, {}, {}, {})
        self.pois = pois
        self.usd = cotizacion
        self.sin_base_alquiler = 0

    # ------------------------------------------------------------ filtros
    def filtro_duro(self, pub, peso) -> str | None:
        """Lo unico que hace desaparecer algo. Deliberadamente corto."""
        exc = self.cfg["exclusiones"]
        if pub.id_fuente in (exc.get("ids") or []):
            return "excluida a mano"
        dir_n = normalizar(pub.direccion)
        for d in (exc.get("direcciones") or []):
            if dir_n and normalizar(d) in dir_n:
                return f"direccion excluida ({d})"
        titulo = normalizar(f"{pub.titulo} {pub.descripcion}")
        for w in (exc.get("palabras") or []):
            if normalizar(w) in titulo:
                return f"palabra excluida ({w})"
        if peso <= 0:
            return f"peso 0 para {pub.tipo} en {pub.barrio}"
        if pub.precio_usd and pub.precio_usd > self.curva_precio(pub.tipo)["max_recoleccion"]:
            return "arriba del maximo de recoleccion"
        if pub.precio_usd and pub.precio_usd < 5000:
            return "precio irreal (probable error de carga)"

        reglas = dict(self.cfg["filtros_duros"].get("activos") or {})
        reglas.update((self.cfg["filtros_duros"].get("por_barrio") or {}).get(pub.barrio, {}))
        if reglas.get("excluir_emprendimientos") and pub.emprendimiento:
            return "emprendimiento"
        tope = reglas.get("sin_expensas_mayores_a")
        if tope and pub.expensas_ars and pub.expensas_ars > tope:
            return f"expensas > {tope:,.0f}"
        maxant = reglas.get("antiguedad_maxima")
        if maxant and pub.antiguedad and pub.antiguedad > maxant:
            return f"antiguedad > {maxant}"

        # Arriba de la mediana de su barrio y su tipo no es una oferta. No se
        # borra: se sigue relevando y anotando el precio, asi que el dia que
        # baje vuelve a aparecer, y aparece con la bajada ya detectada.
        # Lo que no se puede evaluar no se descarta: sin m2 propio o sin
        # mediana del barrio, esto no opina.
        tope_m2 = self.cfg["filtros_duros"].get("sobre_mediana_m2") or 0
        mediana = self.m2.get((pub.barrio, pub.tipo))
        if tope_m2 and mediana and pub.precio_m2 and pub.precio_m2 > mediana * tope_m2:
            return "por encima de la mediana de USD/m2 del barrio"
        return None

    # ------------------------------------------------------------ componentes
    def curva_precio(self, tipo):
        """La franja ideal puede definirse por tipo.

        Una casa a USD 100.000 es barata para ser casa; un departamento a
        USD 100.000 es caro para lo que buscamos. Con una sola curva global,
        casas y PH quedan castigados por ser estructuralmente mas caros.
        """
        base = dict(self.p)
        base.update((self.p.get("por_tipo") or {}).get(tipo, {}))
        return base

    def _precio(self, precio, tipo="depto"):
        p = self.curva_precio(tipo)
        if not precio:
            return 0.4, "sin precio"
        if precio < p["min_puntuable"]:
            return 0.3, f"muy barato (USD {_n(precio)}): revisar que sea real"
        if p["ideal_min"] <= precio <= p["ideal_max"]:
            return 1.0, f"en la franja ideal (USD {_n(precio)})"
        if precio < p["ideal_min"]:
            t = (precio - p["min_puntuable"]) / max(1, p["ideal_min"] - p["min_puntuable"])
            return _clamp(0.6 + 0.4 * t), f"por debajo de la franja (USD {_n(precio)})"
        if precio <= p["cae_desde"]:
            return 0.9, f"apenas arriba de la franja (USD {_n(precio)})"
        if precio <= p["max_puntuable"]:
            t = (precio - p["cae_desde"]) / max(1, p["max_puntuable"] - p["cae_desde"])
            return _clamp(0.7 * (1 - t)), f"caro para el objetivo (USD {_n(precio)})"
        t = (precio - p["max_puntuable"]) / max(1, p["max_recoleccion"] - p["max_puntuable"])
        return _clamp(0.1 * (1 - t)), f"arriba del techo (USD {_n(precio)})"

    def alquiler_de_referencia(self, pub, equipado=False):
        """Alquiler mensual en USD segun lo relevado, del match mas especifico.

        `equipado=True` usa la poblacion de amoblados/temporarios, que es la
        que corresponde para la estrategia tipo AirBnB.
        """
        tabla = self.alq_equipado if equipado else self.alq
        amb = pub.ambientes or (pub.dormitorios + 1 if pub.dormitorios else 0)

        # 1. Match exacto barrio + tipo + ambientes: lo mas confiable.
        for clave in ((pub.barrio, pub.tipo, amb), (pub.barrio, pub.tipo, 0)):
            if clave in tabla:
                return tabla[clave]

        # 2. Alquiler por m2 DEL BARRIO por la superficie real.
        if not equipado and pub.m2 and (pub.barrio, pub.tipo) in self.alq_m2:
            return self.alq_m2[(pub.barrio, pub.tipo)] * pub.m2

        # 3. Alquiler por m2 global, escalado por el nivel de precios del
        #    barrio. Sin este escalado se le ponia alquiler de Palermo a un
        #    departamento de Villa Luro y salian rendimientos de 40%.
        if not equipado and pub.m2 and self._alq_m2_global and self._venta_m2_global:
            venta_barrio = self.m2.get((pub.barrio, pub.tipo))
            factor = (venta_barrio / self._venta_m2_global) if venta_barrio else 1.0
            factor = _clamp(factor, 0.4, 2.0)
            return self._alq_m2_global * factor * pub.m2

        return None

    def _rendimiento(self, pub):
        r = self.s["rendimiento"]
        alquiler = self.alquiler_de_referencia(pub)
        if not alquiler or not pub.precio_usd:
            self.sin_base_alquiler += 1
            return 0.5, None, None, None, "sin base de alquiler todavia (neutral)"
        bruto = alquiler * 12 / pub.precio_usd
        # Para el temporario se usa el alquiler equipado REAL de la zona si lo
        # hay; el premium de config queda solo como respaldo cuando no se
        # relevaron suficientes amoblados en ese barrio.
        equipado = self.alquiler_de_referencia(pub, equipado=True)
        base_temp = equipado or alquiler * r["premium_temporario"]
        temp = base_temp * r["meses_ocupacion_temporario"] / pub.precio_usd
        # Un rendimiento absurdo casi siempre es un dato mal cargado, no una
        # ganga. Se muestra pero se marca, y no se le da puntaje extra.
        implausible = bruto > r.get("implausible_desde", 0.20)
        t = (bruto - r["pobre"]) / max(1e-9, r["excelente"] - r["pobre"])
        if implausible:
            t = 0.5
        nota = (f"rinde {bruto*100:.1f}% bruto anual vacio "
                f"(alquiler estimado USD {_n(alquiler)}/mes)")
        if implausible:
            nota = ("REVISAR: el rendimiento calculado da " + f"{bruto*100:.0f}%"
                    ", implausible. Casi seguro hay un dato mal cargado "
                    "(superficie o precio) en la publicacion")
        if equipado:
            nota += (f"; amoblado USD {_n(equipado)}/mes -> "
                     f"{temp*100:.1f}% con {r['meses_ocupacion_temporario']} meses ocupados")
        return _clamp(t), bruto, temp, alquiler, nota

    def _precio_m2(self, pub):
        mediana = self.m2.get((pub.barrio, pub.tipo))
        if not mediana or not pub.precio_m2:
            return 0.5, "sin mediana de USD/m2 para la zona"
        rel = pub.precio_m2 / mediana
        nota = (f"USD {_n(pub.precio_m2)}/m² vs mediana {_n(mediana)} "
                f"de {pub.tipo} en {pub.barrio} ({(rel-1)*100:+.0f}%)")
        return _clamp(1.5 - rel), nota

    def _ambientes(self, pub):
        ideales = self.s["ambientes_ideales"]
        amb = pub.ambientes or (pub.dormitorios + 1 if pub.dormitorios else None)
        if not amb:
            return 0.5, "ambientes no informados"
        if amb in ideales:
            v = 1.0 - 0.2 * ideales.index(amb)
            plural = "ambiente" if amb == 1 else "ambientes"
            return v, f"{amb} {plural} (buen tamano para renta)"
        return 0.25, f"{amb} ambientes (grande para renta)"

    def expensas_de_referencia(self, pub):
        """ARS/m2 que deberia pagar un edificio parecido a este.

        Baja por tres niveles hasta encontrar muestra: la celda exacta
        (barrio, tipo, banda de superficie), el barrio, y la banda de tamano en
        toda la zona. Cuando cae al nivel del barrio se corrige por tamano con
        la forma de la curva global, porque abajo de 40 m2 se paga ~22% mas por
        m2 que entre 70 y 110: sin esa correccion un monoambiente sale "caro"
        solo por ser chico.

        A proposito NO se cae al ultimo nivel, la mediana del tipo en toda la
        ciudad: comparar un PH de Villa Crespo contra todos los PH de CABA no es
        comparar caracteristicas similares, que es el sentido de esta regla.
        Medido sobre la base, ese nivel lo usaban 172 PH y ningun depto (los
        deptos resuelven 99,4% en su barrio), asi que sacarlo no cuesta nada y
        evita penalizar contra una referencia que no significa nada. Sin
        referencia el componente simplemente no se ajusta.

        Devuelve (esperada, de_donde) o (None, None) si no hay con que comparar.
        """
        if not pub.m2 or not pub.barrio or not pub.tipo:
            return None, None
        celda, barrio, banda, tipo = self.exp_m2
        b = banda_m2(pub.m2, self.s["expensas"]["bandas_m2"])

        v = celda.get((pub.barrio, pub.tipo, b))
        if v:
            return v, f"{pub.barrio}, {pub.tipo}s de tamano parecido"

        v = barrio.get((pub.barrio, pub.tipo))
        if v:
            general, de_banda = tipo.get(pub.tipo), banda.get((pub.tipo, b))
            if general and de_banda:
                v *= _clamp(de_banda / general, 0.6, 1.6)
            return v, f"{pub.tipo}s de {pub.barrio}"

        v = banda.get((pub.tipo, b))
        if v:
            return v, f"{pub.tipo}s de tamano parecido en toda la zona"

        return None, None

    def _expensas_vs_pares(self, pub):
        """Que tan caras son las expensas contra edificios parecidos.

        No es un dato de rentabilidad sino de riesgo: expensas muy por encima
        de los pares suelen venir con algo atras, desde una administracion cara
        hasta una obra o un juicio. Pesa poco porque tambien puede ser
        simplemente un edificio chico o con amenities.

        Devuelve (valor 0..1 o None, ratio o None, nota o None).
        """
        e = self.s["expensas"]
        esperada, de_donde = self.expensas_de_referencia(pub)
        if not esperada:
            return None, None, None
        propia = pub.expensas_ars / pub.m2
        ratio = propia / esperada

        # Un dato fuera de la banda de plausibilidad no dice nada del edificio,
        # dice que el aviso esta mal cargado. No puntua ni para bien ni para
        # mal: se avisa y listo, igual que con los rendimientos de 40%.
        if not e["implausible_bajo"] <= ratio <= e["implausible_alto"]:
            # Se muestra el monto crudo y no el valor por m2: lo que delata el
            # error es "expensas de $1" o "de $15 millones", que redondeado por
            # metro queda en $0 y no se entiende.
            return None, ratio, (f"REVISAR expensas: ${_n(pub.expensas_ars)} por "
                                 f"{_n(pub.m2)} m2 es {ratio:.2f}x lo de "
                                 f"{de_donde}, parece un error de carga")

        if ratio <= e["baratas"]:
            valor = 1.0
        elif ratio >= e["caras"]:
            valor = 0.0
        elif ratio <= 1.0:
            # De 'baratas' a la mediana baja de 1 a 'neutro'.
            t = (ratio - e["baratas"]) / (1.0 - e["baratas"])
            valor = 1.0 - t * (1.0 - e["neutro"])
        else:
            t = (ratio - 1.0) / (e["caras"] - 1.0)
            valor = e["neutro"] * (1.0 - t)

        # El monto va primero y completo. El valor por m2 es el que permite
        # comparar, pero lo que se paga todos los meses es el monto, y ese es el
        # numero con el que uno decide: sin el, el motivo obliga a multiplicar
        # de cabeza para saber de cuanta plata se esta hablando.
        cuanto = f"${_n(pub.expensas_ars)}/mes (${_n(propia)}/m2)"
        nota = None
        if ratio >= e["avisar_caras"]:
            nota = (f"expensas altas: {cuanto}, {ratio:.1f}x las de {de_donde} "
                    f"(${_n(esperada)}/m2)")
        elif ratio <= e["avisar_baratas"]:
            nota = (f"expensas bajas: {cuanto}, {ratio*100:.0f}% de las de "
                    f"{de_donde} (${_n(esperada)}/m2)")
        return _clamp(valor), ratio, nota

    def _expensas(self, pub, alquiler):
        """Cuanto se comen de la renta, ajustado por como estan contra sus pares.

        Las dos senales NO se promedian. Promediarlas hacia que un depto de
        Recoleta con expensas del 36% del alquiler SUBIERA de puntaje, porque
        sus expensas son normales para Recoleta: la comparacion con pares
        terminaba perdonando una carga que ya sabemos que es mala. Que el barrio
        entero sea caro no lo vuelve mejor negocio.

        Asi que la carga manda y los pares la modulan: si las expensas estan muy
        arriba de edificios parecidos la bajan, si estan abajo la suben, y si la
        carga es cero el resultado sigue siendo cero. El ajuste llega como mucho
        a +/-30% del componente, que a su vez pesa 10 de 100: mueve el puntaje
        un par de puntos, que es lo que corresponde a una senal indirecta.

        Devuelve (valor, nota de expensas, nota de la comparacion o None).
        """
        e = self.s["expensas"]
        if not pub.expensas_ars:
            # Ojo: no informarlas no es lo mismo que no tenerlas. En una casa o
            # un PH suele ser que no hay; en un depto, que el aviso no las puso.
            return 0.6, "expensas no informadas", None

        exp_usd = pub.expensas_ars / self.usd
        nota = f"expensas USD {_n(exp_usd)}/mes"
        # Sin alquiler de referencia no hay carga que medir, y el neutro pasa a
        # ser el punto de partida sobre el que actua la comparacion con pares.
        carga = e["neutro"]
        if alquiler:
            ratio = exp_usd / alquiler
            nota = f"expensas USD {_n(exp_usd)} = {ratio*100:.0f}% del alquiler"
            if ratio <= e["comodas"]:
                carga = 1.0
            elif ratio >= e["ahogan"]:
                carga = 0.0
                nota += " (se comen la renta)"
            else:
                carga = _clamp(1 - (ratio - e["comodas"]) / (e["ahogan"] - e["comodas"]))

        # La comparacion con pares va como motivo separado, no pegada a este:
        # asi el aviso de Telegram y la web la pueden destacar por su cuenta.
        pares, _, nota_pares = self._expensas_vs_pares(pub)
        if pares is None:
            return carga, nota, nota_pares

        # pares == neutro deja el ajuste en 1: estar en la mediana no premia ni
        # castiga, solo confirma lo que ya dijo la carga.
        ajuste = 1 + e["peso_pares"] * (pares - e["neutro"]) / e["neutro"]
        return _clamp(carga * ajuste), nota, nota_pares

    def _señales(self, pub, historial, vista_primera):
        """Arranca neutro y se mueve solo con lo que pide historia.

        Hoy queda en 0,5 exacto en el 96,6% de los casos, y no es un defecto:
        de las tres senales, dos necesitan que el sistema lleve tiempo mirando.
        La bajada de precio exige haber visto el aviso dos veces, y 'publicada
        hace mucho' exige 90 dias desde la primera vez que lo vimos, que
        todavia no cumplio ninguna. Este componente se despierta solo con los
        meses; por eso conviene no interrumpir las corridas diarias.

        Los multiplicadores de abajo (0,5 y 0,3) escalan los bonus de config a
        lo que puede moverse desde el neutro: con bonus_bajada_precio=1 el
        aporte es +0,5, que lleva el componente de 0,5 a 1,0 exacto. Estan acá
        y no en el YAML para que config exprese "cuanto importa esta senal" en
        una escala propia, sin tener que saber de donde parte el componente.
        """
        s = self.s["señales"]
        puntos, notas = 0.5, []
        # Un emprendimiento publica muchas unidades bajo un solo aviso y el
        # precio que muestra depende del filtro con que se lo busco: sus
        # cambios de precio son ruido, no senal.
        if historial and len(historial) >= 2 and not pub.emprendimiento:
            primero = next((p for _, p in historial if p), None)
            ultimo = historial[-1][1]
            if primero and ultimo and ultimo < primero * 0.97:
                puntos += s["bonus_bajada_precio"] * 0.5
                notas.append(f"BAJO DE PRECIO: USD {_n(primero)} -> {_n(ultimo)} "
                             f"({(ultimo/primero-1)*100:.0f}%)")
        if vista_primera:
            try:
                dias = (date.today() - date.fromisoformat(vista_primera)).days
                if dias >= s["dias_para_considerar_estancada"]:
                    puntos += s["bonus_publicada_hace_mucho"] * 0.3
                    notas.append(f"publicada hace {dias} dias (vendedor cansado)")
            except ValueError:
                pass
        if pub.antiguedad and pub.antiguedad > s["penaliza_antiguedad_desde"]:
            puntos -= 0.2
            notas.append(f"{pub.antiguedad} anios de antiguedad")
        return _clamp(puntos), "; ".join(notas) or "sin senales particulares"

    # ------------------------------------------------------------ evaluacion
    def evaluar(self, pub, peso_barrio, historial=None, vista_primera=None) -> Resultado:
        res = Resultado()
        motivo = self.filtro_duro(pub, peso_barrio)
        if motivo:
            res.descartada = motivo
            return res

        pesos = self.s["pesos"]
        c = {}
        c["precio"], n_precio = self._precio(pub.precio_usd, pub.tipo)
        (c["rendimiento"], res.rendimiento, res.rendimiento_temporario,
         res.alquiler_estimado, n_rend) = self._rendimiento(pub)
        c["precio_m2"], n_m2 = self._precio_m2(pub)
        c["ambientes"], n_amb = self._ambientes(pub)
        c["expensas"], n_exp, n_pares = self._expensas(pub, res.alquiler_estimado)
        c["señales"], n_sen = self._señales(pub, historial, vista_primera)

        res.merito = sum(c[k] * pesos[k] for k in pesos)
        res.componentes = c

        bonus, cerca = self.pois.bonus(pub.lat, pub.lon)
        res.peso_zona = peso_barrio
        res.peso_efectivo = min(10.0, peso_barrio + bonus)
        res.factor_zona = (res.peso_efectivo / 10.0) ** self.s["exponente_zona"]
        res.puntaje = res.merito * res.factor_zona
        res.pois_cerca = cerca

        # Se filtran los vacios acá: mas abajo hay quien hace m.startswith(...)
        # sobre esta lista y un None la rompe.
        res.motivos = [m for m in (n_rend, n_precio, n_m2, n_amb, n_exp,
                                   n_pares, n_sen) if m]
        if cerca:
            res.motivos.append("cerca de " + ", ".join(
                f"{n} ({d} m)" for n, d, _ in cerca[:3]))
        if bonus > 0.1:
            res.motivos.append(
                f"peso de zona {peso_barrio:.0f} -> {res.peso_efectivo:.1f} "
                f"por cercania (+{bonus:.1f})")
        return res
