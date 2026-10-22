"""Base SQLite: el activo real del proyecto.

Sin historico no hay 'nuevo', no hay 'bajo de precio' y no hay mediana de
zona, que son las tres cosas que ningun portal te da.
"""
from __future__ import annotations

import json
import sqlite3
import statistics
from datetime import date, datetime
from pathlib import Path

ESQUEMA = """
CREATE TABLE IF NOT EXISTS publicaciones (
    clave           TEXT PRIMARY KEY,
    fuente          TEXT NOT NULL,
    id_fuente       TEXT NOT NULL,
    huella          TEXT,
    url             TEXT,
    operacion       TEXT,
    tipo            TEXT,
    titulo          TEXT,
    barrio          TEXT,
    direccion       TEXT,
    precio_usd      REAL,
    moneda          TEXT,
    expensas_ars    REAL,
    m2_cubiertos    REAL,
    m2_totales      REAL,
    ambientes       INTEGER,
    dormitorios     INTEGER,
    antiguedad      INTEGER,
    emprendimiento  INTEGER DEFAULT 0,
    descripcion     TEXT,
    foto            TEXT,
    lat             REAL,
    lon             REAL,
    geo_confianza   TEXT,
    geo_motivo      TEXT,
    geo_dudosa      INTEGER DEFAULT 0,
    vista_primera   TEXT,
    vista_ultima    TEXT,
    activa          INTEGER DEFAULT 1
);
CREATE INDEX IF NOT EXISTS ix_pub_huella   ON publicaciones(huella);
CREATE INDEX IF NOT EXISTS ix_pub_op       ON publicaciones(operacion, tipo, barrio);
CREATE INDEX IF NOT EXISTS ix_pub_activa   ON publicaciones(activa, operacion);

CREATE TABLE IF NOT EXISTS precios (
    clave       TEXT NOT NULL,
    fecha       TEXT NOT NULL,
    precio_usd  REAL,
    expensas    REAL,
    PRIMARY KEY (clave, fecha)
);

CREATE TABLE IF NOT EXISTS geocache (
    direccion TEXT PRIMARY KEY,
    lat REAL, lon REAL, fecha TEXT
);

CREATE TABLE IF NOT EXISTS corridas (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    inicio TEXT, fin TEXT, resumen TEXT
);

-- Lo que ya se aviso por Telegram. 'mensaje_id' es lo que permite contestar
-- un aviso con texto y que la nota caiga en la propiedad correcta.
CREATE TABLE IF NOT EXISTS notificadas (
    clave TEXT PRIMARY KEY, fecha TEXT, puntaje REAL, mensaje_id INTEGER
);

-- Marcas hechas desde Telegram o desde la web. NADA se borra: una propiedad
-- marcada "no me interesa" sigue en la base y se puede consultar filtrando por
-- ese estado. Se guardan con la firma geografica ademas de la clave, para que
-- si la misma propiedad reaparece publicada por otra inmobiliaria o en otro
-- portal la marca la siga.
-- La PK lleva el usuario aunque hoy haya uno solo: asi el esquema no cambia si
-- algun dia se comparte.
CREATE TABLE IF NOT EXISTS marcas (
    clave      TEXT NOT NULL,
    usuario    TEXT NOT NULL,     -- hoy siempre USUARIO; la PK lo deja listo para mas
    estado     TEXT NOT NULL,     -- 'interesa' | 'no_interesa'
    fecha      TEXT,
    quien      TEXT,              -- el nombre para mostrar; el mail es la clave
    origen     TEXT,              -- 'telegram' | 'web'
    direccion  TEXT,
    lat REAL, lon REAL,
    precio_usd REAL,
    ambientes  INTEGER,
    edificio   INTEGER DEFAULT 0, -- 1 = aplica a todo el edificio, no a la unidad
    nota       TEXT,              -- por que la marco asi; opcional
    PRIMARY KEY (clave, usuario)
);

-- Que un aviso se haya caido NO es una opinion, es un hecho: vale para todos y
-- por eso vive aparte de las marcas, sin usuario.
CREATE TABLE IF NOT EXISTS caidas (
    clave  TEXT PRIMARY KEY,
    fecha  TEXT,
    quien  TEXT               -- quien se dio cuenta, para poder preguntarle
);
CREATE INDEX IF NOT EXISTS ix_marcas_estado ON marcas(estado);

-- Lo que muestra la web local: el ranking ya puntuado. Lo escribe `informe`
-- (ver ranking.py) y la web solo lo lee, asi no recalcula nada al abrir una
-- pagina. Es descartable: se rehace corriendo `informe`.
CREATE TABLE IF NOT EXISTS ranking (
    clave TEXT PRIMARY KEY, fuente TEXT, url TEXT, titulo TEXT, foto TEXT,
    tipo TEXT, barrio TEXT, direccion TEXT, lat REAL, lon REAL,
    geo_dudosa INTEGER DEFAULT 0,
    precio_usd REAL, precio_m2 REAL, expensas_ars REAL, m2 REAL,
    ambientes INTEGER, dormitorios INTEGER, antiguedad INTEGER,
    emprendimiento INTEGER DEFAULT 0,
    puntaje REAL NOT NULL, rendimiento REAL, rendimiento_temp REAL,
    alquiler_estimado REAL, peso_zona REAL, peso_efectivo REAL,
    motivos TEXT, pois_cerca TEXT,
    edificio TEXT, en_edificio INTEGER DEFAULT 1, en_edificio_total INTEGER DEFAULT 1,
    edificio_exacto INTEGER DEFAULT 1, copias INTEGER DEFAULT 1, urls_copias TEXT,
    bajo_precio INTEGER DEFAULT 0, precio_anterior REAL,
    vista_primera TEXT, vista_ultima TEXT, actualizada TEXT
);
CREATE INDEX IF NOT EXISTS ix_ranking_puntaje ON ranking(puntaje);
CREATE INDEX IF NOT EXISTS ix_ranking_edificio ON ranking(edificio);
"""

# El motor es de una sola persona: todas las marcas (las de Telegram y las de la
# web local) son de este usuario, asi que las dos vistas ven lo mismo.
USUARIO = "yo"

CAMPOS = ("fuente id_fuente huella url operacion tipo titulo barrio direccion "
          "precio_usd moneda expensas_ars m2_cubiertos m2_totales ambientes "
          "dormitorios antiguedad emprendimiento descripcion foto lat lon "
          "geo_confianza geo_motivo geo_dudosa").split()


# Marca para "no mandaste nota, no la toques" (distinto de "nota vacia = borrala").
NO_TOCAR = object()


def banda_m2(m2, bandas=(40, 70, 110)):
    """Indice de la banda de superficie a la que cae m2. Pura, se usa desde scoring."""
    return sum(1 for b in bandas if m2 >= b)


class Base:
    def __init__(self, ruta: Path, usuario: str = USUARIO):
        # El usuario es quien es "yo" para el motor: el informe y los avisos de
        # Telegram salen con SUS marcas.
        self.usuario = usuario
        ruta.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(ruta, timeout=30)
        self.con.row_factory = sqlite3.Row
        # WAL porque ahora hay mas de un proceso sobre la misma base: el bot
        # queda escuchando todo el dia y la web local lee y marca a la vez.
        # Con el journal por defecto una escritura bloquea a las lecturas y el
        # que llega segundo se lleva un "database is locked". Con WAL leen y
        # escriben en paralelo, y el timeout cubre el cruce de dos escrituras.
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=30000")
        self.con.executescript(ESQUEMA)
        self._migrar()
        self.con.commit()

    def _migrar(self):
        """Agrega columnas nuevas a una base que ya existe."""
        existentes = {r[1] for r in self.con.execute("PRAGMA table_info(publicaciones)")}
        for columna, tipo in (("foto", "TEXT"),):
            if columna not in existentes:
                self.con.execute(f"ALTER TABLE publicaciones ADD COLUMN {columna} {tipo}")
        self._migrar_marcas_por_usuario()
        if "nota" not in {r[1] for r in self.con.execute("PRAGMA table_info(marcas)")}:
            self.con.execute("ALTER TABLE marcas ADD COLUMN nota TEXT")
        if "mensaje_id" not in {r[1] for r in
                                self.con.execute("PRAGMA table_info(notificadas)")}:
            self.con.execute("ALTER TABLE notificadas ADD COLUMN mensaje_id INTEGER")

    def _migrar_marcas_por_usuario(self):
        """Marcas de una sola lista -> una lista por persona, y caidas aparte.

        SQLite no sabe cambiar una PRIMARY KEY, asi que la tabla se rehace. Las
        marcas que ya estaban son todas del usuario (hasta ahora la web y el bot
        eran de una sola persona), asi que se le adjudican a ella.
        """
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(marcas)")}
        if not cols or "usuario" in cols:
            return
        self.con.executescript("""
            ALTER TABLE marcas RENAME TO marcas_vieja;
            CREATE TABLE marcas (
                clave TEXT NOT NULL, usuario TEXT NOT NULL, estado TEXT NOT NULL,
                fecha TEXT, quien TEXT, origen TEXT, direccion TEXT,
                lat REAL, lon REAL, precio_usd REAL, ambientes INTEGER,
                edificio INTEGER DEFAULT 0, nota TEXT,
                PRIMARY KEY (clave, usuario));
            CREATE TABLE IF NOT EXISTS caidas (
                clave TEXT PRIMARY KEY, fecha TEXT, quien TEXT);
        """)
        self.con.execute(
            "INSERT INTO caidas (clave, fecha, quien) "
            "SELECT clave, fecha, quien FROM marcas_vieja WHERE estado='caida'")
        self.con.execute(
            "INSERT INTO marcas (clave,usuario,estado,fecha,quien,origen,direccion,"
            " lat,lon,precio_usd,ambientes,edificio) "
            "SELECT clave,?,estado,fecha,quien,origen,direccion,lat,lon,precio_usd,"
            " ambientes,edificio FROM marcas_vieja WHERE estado<>'caida'",
            (self.usuario,))
        self.con.executescript(
            "DROP TABLE marcas_vieja;"
            "CREATE INDEX IF NOT EXISTS ix_marcas_estado ON marcas(estado);"
            "CREATE INDEX IF NOT EXISTS ix_marcas_usuario ON marcas(usuario);")

    def cerrar(self):
        self.con.commit()
        self.con.close()

    # ---------------------------------------------------------------- guardar
    def guardar(self, pub, ubic) -> str:
        """Inserta o actualiza. Devuelve 'nueva' | 'bajo_precio' | 'subio' | 'igual'."""
        hoy = date.today().isoformat()
        cur = self.con.execute(
            "SELECT precio_usd, vista_primera FROM publicaciones WHERE clave=?",
            (pub.clave,))
        previo = cur.fetchone()

        datos = {
            **{c: getattr(pub, c, None) for c in CAMPOS if hasattr(pub, c)},
            "huella": pub.huella,
            "emprendimiento": int(pub.emprendimiento),
            "geo_confianza": ubic.confianza,
            "geo_motivo": ubic.motivo,
            "geo_dudosa": int(ubic.dudosa),
            "vista_ultima": hoy,
            "activa": 1,
        }

        if previo is None:
            datos["vista_primera"] = hoy
            cols = ["clave"] + list(datos)
            self.con.execute(
                f"INSERT INTO publicaciones ({','.join(cols)}) "
                f"VALUES ({','.join('?' * len(cols))})",
                [pub.clave] + [datos[c] for c in datos])
            estado = "nueva"
        else:
            sets = ",".join(f"{c}=?" for c in datos)
            self.con.execute(f"UPDATE publicaciones SET {sets} WHERE clave=?",
                             list(datos.values()) + [pub.clave])
            anterior = previo["precio_usd"]
            # En un emprendimiento el precio publicado es el de alguna de sus
            # unidades, no siempre la misma, asi que un cambio no significa
            # nada. Ver fuentes/zonaprop.py.
            if pub.emprendimiento:
                estado = "igual"
            elif anterior and pub.precio_usd:
                if pub.precio_usd < anterior * 0.985:
                    estado = "bajo_precio"
                elif pub.precio_usd > anterior * 1.015:
                    estado = "subio"
                else:
                    estado = "igual"
            else:
                estado = "igual"

        if pub.precio_usd:
            self.con.execute(
                "INSERT OR REPLACE INTO precios (clave,fecha,precio_usd,expensas) "
                "VALUES (?,?,?,?)",
                (pub.clave, hoy, pub.precio_usd, pub.expensas_ars))
        return estado

    def marcar_inactivas(self, fuente: str, dias: int = 10):
        """Lo que hace dias que no aparece probablemente se vendio o se bajo."""
        self.con.execute(
            "UPDATE publicaciones SET activa=0 WHERE fuente=? AND activa=1 "
            "AND julianday('now') - julianday(vista_ultima) > ?", (fuente, dias))

    # ----------------------------------------------------------------- lectura
    def historial_precio(self, clave):
        return [(r["fecha"], r["precio_usd"]) for r in self.con.execute(
            "SELECT fecha,precio_usd FROM precios WHERE clave=? ORDER BY fecha",
            (clave,))]

    def activas(self, operacion="venta"):
        """Lo que sigue en pie: activo y sin aviso caido.

        El filtro de caidas va aca y no en cada consumidor porque la
        recoleccion las vuelve a prender: guardar() reescribe la fila con
        activa=1 y, sin esto, el aviso muerto reaparece al dia siguiente en el
        informe, en Telegram y en la web.
        """
        return list(self.con.execute(
            "SELECT * FROM publicaciones WHERE activa=1 AND operacion=? "
            "AND clave NOT IN (SELECT clave FROM caidas)", (operacion,)))

    # Un departamento amoblado o con amenities alquila MUCHISIMO mas caro que
    # uno vacio: en Palermo 2 ambientes el rango relevado va de USD 293 a 1.853.
    # Mezclarlos infla la mediana y sobreestima el rendimiento de todo (se veian
    # 14% donde hay 10%). Se separan en dos poblaciones, que ademas es lo que
    # hace falta para distinguir la estrategia de alquiler vacio de la de
    # temporario.
    PALABRAS_EQUIPADO = ("amoblad", "amueblad", "temporari", "equipad",
                         "furnished", "por dia", "diario", "turist")

    @classmethod
    def _equipado(cls, titulo, descripcion):
        texto = f"{titulo or ''} {descripcion or ''}".lower()
        return any(p in texto for p in cls.PALABRAS_EQUIPADO)

    # Cotas por defecto para lo que puede ser un alquiler mensual de verdad.
    # Quien llama pasa las de config.yaml; estas son el respaldo.
    ALQUILER_MIN_USD, ALQUILER_MAX_USD = 150, 20000

    def _alquileres(self, columnas, minimo_usd=None, maximo_usd=None):
        """Los alquileres que pueden ser un alquiler, y cuantos se dejaron afuera.

        En los portales entra basura con forma de alquiler: cocheras y modulos
        odontologicos por abajo, y sobre todo avisos de 'venta y alquiler' donde
        el listado de alquileres muestra el precio de VENTA. Un alquiler de USD
        300.000 mensuales en una celda con pocas muestras corre la mediana, y de
        esa mediana sale el rendimiento, que es el corazon del puntaje.
        """
        bajo = self.ALQUILER_MIN_USD if minimo_usd is None else minimo_usd
        alto = self.ALQUILER_MAX_USD if maximo_usd is None else maximo_usd
        filas = list(self.con.execute(
            f"SELECT {columnas} FROM publicaciones "
            "WHERE activa=1 AND operacion='alquiler' AND precio_usd>0"))
        buenas = [f for f in filas if bajo <= f["precio_usd"] <= alto]
        return buenas, len(filas) - len(buenas)

    def medianas_alquiler(self, minimo=4, minimo_usd=None, maximo_usd=None):
        """Medianas de alquiler en USD, separadas por tipo de producto.

        Devuelve (vacio, equipado), cada uno {(barrio, tipo, ambientes): mediana}.

        Es lo que convierte la herramienta en algo util: sin esto no se puede
        estimar rendimiento y 'oferta' vuelve a ser solo 'precio bajo'.
        """
        filas, _ = self._alquileres(
            "barrio,tipo,ambientes,precio_usd,titulo,descripcion",
            minimo_usd, maximo_usd)
        vacio, equipado = {}, {}
        for f in filas:
            destino = equipado if self._equipado(f["titulo"], f["descripcion"]) else vacio
            clave = (f["barrio"], f["tipo"], f["ambientes"] or 0)
            destino.setdefault(clave, []).append(f["precio_usd"])
        med = lambda g: {k: statistics.median(v) for k, v in g.items() if len(v) >= minimo}
        return med(vacio), med(equipado)

    def alquileres_descartados(self, minimo_usd=None, maximo_usd=None):
        """Cuantos alquileres quedan afuera por implausibles. Para el informe:
        si el numero crece de golpe, algo cambio en los portales."""
        return self._alquileres("precio_usd", minimo_usd, maximo_usd)[1]

    def medianas_alquiler_m2(self, minimo=4, minimo_usd=None, maximo_usd=None):
        """{(barrio, tipo): mediana de USD/m2 de alquiler mensual}.

        Es mucho mas transferible que la mediana por cantidad de ambientes: no
        hay alquileres relevados de 5 ambientes en Villa Luro, pero el precio
        por m2 del barrio si se conoce y escala bien con el tamano.
        """
        filas, _ = self._alquileres(
            "barrio,tipo,precio_usd,m2_cubiertos,m2_totales,titulo,descripcion",
            minimo_usd, maximo_usd)
        grupos = {}
        for f in filas:
            if self._equipado(f["titulo"], f["descripcion"]):
                continue                      # solo alquiler vacio
            m2 = f["m2_cubiertos"] or f["m2_totales"]
            if m2 and 15 <= m2 <= 400:
                grupos.setdefault((f["barrio"], f["tipo"]), []).append(f["precio_usd"] / m2)
        return {k: statistics.median(v) for k, v in grupos.items() if len(v) >= minimo}

    def medianas_expensas_m2(self, bandas=(40, 70, 110), minimo=8, recorte=2,
                             plausible=(0.15, 4.0), dispersion_maxima=2.5):
        """Expensas en ARS/m2 por mes, para comparar un edificio con sus pares.

        Devuelve cuatro niveles, del mas especifico al mas general:
            (por_celda, por_barrio, por_banda, por_tipo)
        con claves (barrio, tipo, banda), (barrio, tipo), (tipo, banda) y tipo.
        Quien consulta baja de nivel hasta encontrar muestra suficiente.

        Tres decisiones que los datos obligaron:

        1. Se separa por BANDA DE SUPERFICIE. Las expensas por m2 no son planas:
           medidas sobre 9.938 deptos dan 4.107 ARS/m2 abajo de 40 m2 y 3.219
           entre 70 y 110. Los costos fijos del edificio se reparten entre menos
           metros. Comparar sin corregir castigaria a los monoambientes, que son
           justo el producto de renta que se busca.

        2. Se separa por TIPO. La mediana del PH es 1.395 y la del depto 3.800:
           un PH casi no tiene servicios centrales que pagar.

        3. Se LIMPIA LA MUESTRA en dos pasos. Primero se saca lo que esta fuera
           de la banda de plausibilidad respecto de la mediana cruda del tipo:
           hay avisos con expensas de $15 millones en un 2 ambientes y otros
           con expensas de $1. Despues se recorta un percentil de cada cola.
           Los dos criterios son relativos, no montos fijos, asi que no hay que
           tocarlos cuando la inflacion mueve todo de lugar.

           El primer paso importa sobre todo para el PH: como muchos no tienen
           expensas y el aviso pone un monto simbolico, sin limpiar la mediana
           del grupo se hunde y despues cualquier PH con expensas reales
           aparece como "9 veces sus pares".

        4. Se DESCARTA EL GRUPO ENTERO SI ESTA MUY DISPERSO (p75/p25 arriba de
           'dispersion_maxima'). Un grupo asi no es una referencia: no tiene un
           valor tipico contra el cual comparar.

           Esto saca al PH casi por completo, y con razon. Solo el 16% informa
           expensas, y de esos un 34% paga menos de 1.000 ARS/m2 contra el 3%
           de los deptos: son dos poblaciones mezcladas, el PH en consorcio y
           el que no lo esta, y desde el aviso no hay como separarlas. Sus
           cuartiles se llevan 3 veces (750 y 2.222) contra 1,9 del depto, asi
           que "el doble de la mediana" en PH cae apenas arriba del p75 y no
           dice nada, mientras que en depto es el p95. El depto en cambio
           siempre esta en consorcio: es una poblacion homogenea y comparable.

           Se mide asi, y no excluyendo el PH a mano, porque el criterio real
           es "este grupo tiene un valor tipico o no", y eso se sostiene solo
           si manana cambia el mix de lo que publican los portales.

        Entran venta y alquiler juntos: las expensas son del edificio, no de la
        operacion, y sumar los alquileres agranda la muestra un 27%.
        """
        filas = self.con.execute(
            "SELECT barrio,tipo,expensas_ars,m2_cubiertos,m2_totales "
            "FROM publicaciones WHERE activa=1 AND expensas_ars>0")
        crudo = []
        for f in filas:
            m2 = f["m2_cubiertos"] or f["m2_totales"]
            if m2 and 15 <= m2 <= 400:
                crudo.append((f["barrio"], f["tipo"], banda_m2(m2, bandas),
                              f["expensas_ars"] / m2))

        # Paso 1: la mediana cruda de cada tipo define que es un valor posible.
        # La mediana aguanta bien los disparates, asi que sirve de vara aun
        # calculada sobre la muestra sucia.
        bajo, alto = plausible
        por_tipo_crudo = {}
        for _, t, _, r in crudo:
            por_tipo_crudo.setdefault(t, []).append(r)
        limites = {t: (statistics.median(v) * bajo, statistics.median(v) * alto)
                   for t, v in por_tipo_crudo.items()}

        celda, barrio, banda, tipo = {}, {}, {}, {}
        for b_rrio, t, b, r in crudo:
            lim = limites.get(t)
            if lim and not lim[0] <= r <= lim[1]:
                continue
            celda.setdefault((b_rrio, t, b), []).append(r)
            barrio.setdefault((b_rrio, t), []).append(r)
            banda.setdefault((t, b), []).append(r)
            tipo.setdefault(t, []).append(r)

        def med(grupos, minimo_grupo):
            salida = {}
            for k, v in grupos.items():
                if len(v) < minimo_grupo:
                    continue
                v = sorted(v)
                corte = int(len(v) * recorte / 100)
                if corte:
                    v = v[corte:len(v) - corte]
                if not v:
                    continue
                p25, p75 = v[len(v) // 4], v[len(v) * 3 // 4]
                if not p25 or p75 / p25 > dispersion_maxima:
                    continue
                salida[k] = statistics.median(v)
            return salida

        return med(celda, minimo), med(barrio, minimo), med(banda, minimo), med(tipo, minimo)

    def medianas_precio_m2(self):
        """{(barrio, tipo): mediana USD/m2} sobre las ventas activas."""
        filas = self.con.execute(
            "SELECT barrio,tipo,precio_usd,m2_cubiertos,m2_totales FROM publicaciones "
            "WHERE activa=1 AND operacion='venta' AND precio_usd>0")
        grupos = {}
        for f in filas:
            m2 = f["m2_cubiertos"] or f["m2_totales"]
            if m2 and m2 > 10:
                grupos.setdefault((f["barrio"], f["tipo"]), []).append(f["precio_usd"] / m2)
        return {k: statistics.median(v) for k, v in grupos.items() if len(v) >= 5}

    def resumen(self):
        q = lambda s, *a: self.con.execute(s, a).fetchone()[0]
        return {
            "venta_activas": q("SELECT COUNT(*) FROM publicaciones WHERE activa=1 AND operacion='venta'"),
            "alquiler_activas": q("SELECT COUNT(*) FROM publicaciones WHERE activa=1 AND operacion='alquiler'"),
            "total_historico": q("SELECT COUNT(*) FROM publicaciones"),
            "con_coordenadas": q("SELECT COUNT(*) FROM publicaciones WHERE lat IS NOT NULL AND activa=1"),
            "observaciones_precio": q("SELECT COUNT(*) FROM precios"),
            "corridas": q("SELECT COUNT(*) FROM corridas"),
        }

    # ---------------------------------------------------------------- marcas
    # Los dos estados son opiniones, y son de una persona. Que el aviso se haya
    # caido no es una opinion y vale para todos: vive en 'caidas'.
    ESTADOS = ("interesa", "no_interesa")

    def marcar(self, pub, estado, usuario=None, quien=None, origen="telegram",
               edificio=False, nota=NO_TOCAR):
        """Guarda la marca. Con nota=NO_TOCAR conserva la nota que ya tenia:
        cambiar de opinion no borra el motivo; nota=None (o vacia) si lo borra."""
        if estado not in self.ESTADOS:
            raise ValueError(f"estado invalido: {estado}")
        usuario = usuario or self.usuario
        if nota is NO_TOCAR:
            previa = self.con.execute(
                "SELECT nota FROM marcas WHERE clave=? AND usuario=?",
                (pub.clave, usuario)).fetchone()
            nota = previa["nota"] if previa else None
        self.con.execute(
            "INSERT OR REPLACE INTO marcas "
            "(clave,usuario,estado,fecha,quien,origen,direccion,lat,lon,precio_usd,"
            " ambientes,edificio,nota) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (pub.clave, usuario, estado,
             datetime.now().isoformat(timespec="seconds"),
             quien, origen, pub.direccion, pub.lat, pub.lon, pub.precio_usd,
             pub.ambientes, int(edificio), nota))
        self.con.commit()

    # ---------------------------------------------------------------- caidas
    def marcar_caida(self, clave, quien=None):
        """El aviso ya no existe. Vale para todos, asi que no lleva usuario."""
        self.con.execute(
            "INSERT OR REPLACE INTO caidas (clave, fecha, quien) VALUES (?,?,?)",
            (clave, datetime.now().isoformat(timespec="seconds"), quien))
        self.desactivar(clave)
        self.con.commit()

    def cargar_caidas(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM caidas")]

    def es_caida(self, clave):
        return self.con.execute("SELECT 1 FROM caidas WHERE clave=?",
                                (clave,)).fetchone() is not None

    def levantar_caida(self, clave):
        """Se marco caido por error: vuelve a circular."""
        cur = self.con.execute("DELETE FROM caidas WHERE clave=?", (clave,))
        self.con.commit()
        return cur.rowcount

    def desactivar(self, clave):
        """Saca una publicacion de circulacion sin borrar su historial.

        Se usa cuando el aviso resulto estar caido. No se borra la fila: los
        precios historicos siguen sirviendo para detectar bajadas de otras
        publicaciones del mismo edificio, y si el aviso vuelve a aparecer en la
        proxima recoleccion la marca 'caida' lo mantiene afuera igual.
        """
        cur = self.con.execute("UPDATE publicaciones SET activa=0 WHERE clave=?",
                               (clave,))
        self.con.commit()
        return cur.rowcount

    def reactivar(self, clave):
        """Deshace un desactivar(): la publicacion vuelve al ranking."""
        cur = self.con.execute("UPDATE publicaciones SET activa=1 WHERE clave=?",
                               (clave,))
        self.con.commit()
        return cur.rowcount

    def desmarcar(self, clave, usuario=None):
        """Borra la marca de una persona. Sin usuario, la de todas."""
        if usuario:
            cur = self.con.execute("DELETE FROM marcas WHERE clave=? AND usuario=?",
                                   (clave, usuario))
        else:
            cur = self.con.execute("DELETE FROM marcas WHERE clave=?", (clave,))
        self.con.commit()
        return cur.rowcount

    def cargar_marcas(self):
        return [dict(r) for r in self.con.execute("SELECT * FROM marcas")]

    def migrar_descartes(self):
        """Los descartes viejos pasan a ser marcas de 'no me interesa'."""
        try:
            viejos = list(self.con.execute("SELECT * FROM descartes"))
        except Exception:
            return 0
        for d in viejos:
            self.con.execute(
                "INSERT OR IGNORE INTO marcas "
                "(clave,usuario,estado,fecha,quien,origen,direccion,lat,lon,"
                " precio_usd,ambientes,edificio) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                (d["clave"], self.usuario, "no_interesa", d["fecha"], None, "telegram",
                 d["direccion"], d["lat"], d["lon"], d["precio_usd"],
                 d["ambientes"], d["edificio"]))
        self.con.execute("DROP TABLE IF EXISTS descartes")
        self.con.commit()
        return len(viejos)

    # -------------------------------------------------------------- avisos
    def ya_notificada(self, clave):
        return self.con.execute("SELECT 1 FROM notificadas WHERE clave=?",
                                (clave,)).fetchone() is not None

    def marcar_notificada(self, clave, puntaje, mensaje_id=None):
        self.con.execute(
            "INSERT OR REPLACE INTO notificadas (clave,fecha,puntaje,mensaje_id) "
            "VALUES (?,?,?,?)",
            (clave, date.today().isoformat(), puntaje, mensaje_id))
        self.con.commit()

    def clave_de_mensaje(self, mensaje_id):
        """De que propiedad hablaba ese aviso. Es como una respuesta en
        Telegram se convierte en una nota sobre la propiedad correcta."""
        r = self.con.execute("SELECT clave FROM notificadas WHERE mensaje_id=?",
                             (mensaje_id,)).fetchone()
        return r["clave"] if r else None

    def anotar(self, clave, usuario, texto):
        """Escribe la nota sobre una marca que ya existe.

        La nota vive en la marca, asi que anotar algo que todavia no se marco
        no tiene donde ir: devuelve 0 y quien llama avisa que hay que marcarlo
        primero.
        """
        cur = self.con.execute(
            "UPDATE marcas SET nota=?, fecha=? WHERE clave=? AND usuario=?",
            (texto or None, datetime.now().isoformat(timespec="seconds"),
             clave, usuario))
        self.con.commit()
        return cur.rowcount

    # -------------------------------------------------------------- geocache
    def geocache_leer(self, direccion):
        r = self.con.execute("SELECT lat,lon FROM geocache WHERE direccion=?",
                             (direccion,)).fetchone()
        return (r["lat"], r["lon"]) if r else None

    def geocache_guardar(self, direccion, lat, lon):
        self.con.execute(
            "INSERT OR REPLACE INTO geocache VALUES (?,?,?,?)",
            (direccion, lat, lon, datetime.now().isoformat(timespec="seconds")))

    # -------------------------------------------------------------- corridas
    def registrar_corrida(self, inicio, resumen: dict):
        self.con.execute(
            "INSERT INTO corridas (inicio,fin,resumen) VALUES (?,?,?)",
            (inicio, datetime.now().isoformat(timespec="seconds"),
             json.dumps(resumen, ensure_ascii=False)))
        self.con.commit()

    def es_primera_corrida(self) -> bool:
        return self.con.execute("SELECT COUNT(*) FROM corridas").fetchone()[0] <= 1
