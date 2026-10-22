"""Aviso por Telegram.

Dos comandos:

    python -m inmobiliario avisar     # manda lo nuevo que supere el umbral
    python -m inmobiliario escuchar   # atiende los botones de "descartar"

Criterio de aviso, en orden de importancia:

  1. Nunca se repite una publicacion ya avisada.
  2. Una BAJADA DE PRECIO avisa aunque no llegue al umbral: es la senal mas
     valiosa que tenemos y no la da ningun portal.
  3. Se manda un lote por corrida, no un mensaje por publicacion. La diferencia
     entre una herramienta que se usa seis meses y una que se silencia a los
     tres dias es casi siempre esa.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

import requests

API = "https://api.telegram.org/bot{token}/{metodo}"


class Bot:
    def __init__(self, token, chat_id, timeout=25):
        self.token, self.chat_id, self.timeout = token, chat_id, timeout

    def _llamar(self, metodo, **datos):
        r = requests.post(API.format(token=self.token, metodo=metodo),
                          json=datos, timeout=self.timeout)
        d = r.json()
        if not d.get("ok"):
            raise RuntimeError(f"Telegram rechazo {metodo}: {d.get('description')}")
        return d["result"]

    def yo(self):
        return self._llamar("getMe")

    def mensaje(self, texto, botones=None):
        datos = {"chat_id": self.chat_id, "text": texto,
                 "parse_mode": "HTML", "disable_web_page_preview": True}
        if botones:
            datos["reply_markup"] = {"inline_keyboard": botones}
        return self._llamar("sendMessage", **datos)

    def foto(self, url, pie, botones=None, log=None):
        """Manda la portada con la ficha como pie.

        Si Telegram no puede bajar la imagen (el portal cambia de CDN, pone
        hotlink protection o el aviso se dio de baja) se cae a un mensaje de
        texto: perder la foto no puede costar el aviso entero.
        """
        datos = {"chat_id": self.chat_id, "photo": url, "caption": pie,
                 "parse_mode": "HTML"}
        if botones:
            datos["reply_markup"] = {"inline_keyboard": botones}
        try:
            return self._llamar("sendPhoto", **datos)
        except Exception as e:
            # Que quede rastro: si un portal empieza a bloquear el hotlinking,
            # las fotos desaparecerian sin que nadie se entere.
            if log:
                log(f"    ! sin foto ({url[:60]}): {e}")
            return self.mensaje(pie, botones)

    def actualizaciones(self, offset=None, espera=25):
        try:
            return self._llamar("getUpdates", offset=offset, timeout=espera)
        except Exception:
            return []

    def responder_boton(self, callback_id, texto):
        try:
            self._llamar("answerCallbackQuery", callback_query_id=callback_id,
                         text=texto)
        except Exception:
            pass

    def editar(self, chat_id, message_id, texto, botones=None, es_foto=False):
        """Reescribe un mensaje ya enviado.

        Un mensaje con foto lleva `caption`, no `text`, y hay que editarlo con
        editMessageCaption: editMessageText falla sobre esos. Es lo que rompio
        la confirmacion visual cuando se agregaron las fotos.
        """
        datos = {"chat_id": chat_id, "message_id": message_id,
                 "parse_mode": "HTML"}
        if botones is not None:
            datos["reply_markup"] = {"inline_keyboard": botones}
        try:
            if es_foto:
                self._llamar("editMessageCaption", caption=texto, **datos)
            else:
                self._llamar("editMessageText", text=texto,
                             disable_web_page_preview=True, **datos)
        except Exception:
            pass


# --------------------------------------------------------------- credenciales
def credenciales(raiz: Path):
    ruta = raiz / "config" / "credenciales.json"
    return json.loads(ruta.read_text(encoding="utf-8")) if ruta.exists() else {}


def guardar_credenciales(raiz: Path, datos):
    ruta = raiz / "config" / "credenciales.json"
    ruta.write_text(json.dumps(datos, indent=2), encoding="utf-8")
    try:
        ruta.chmod(0o600)      # en Windows no hace nada, y no importa
    except OSError:
        pass


def configurar(cfg, log=print):
    """Alta interactiva del bot. Se corre una sola vez."""
    raiz = cfg["_raiz"]
    d = credenciales(raiz)
    tg = d.get("telegram") or {}

    if not tg.get("token"):
        log("\n  Crea el bot con @BotFather en Telegram:")
        log("    1. Mandale /newbot")
        log("    2. Elegi un nombre y un username terminado en 'bot'")
        log("    3. Copia el token que te devuelve\n")
        tg["token"] = input("  Token: ").strip()

    bot = Bot(tg["token"], None)
    try:
        yo = bot.yo()
        log(f"  Bot conectado: @{yo.get('username')}")
    except Exception as e:
        log(f"  ! El token no funciona: {e}")
        return None

    if not tg.get("chat_id"):
        log("\n  Ahora abri el chat con el bot y mandale cualquier mensaje "
            "(por ejemplo /start).")
        log("  Esperando...")
        limite = time.time() + 180
        while time.time() < limite:
            for u in bot.actualizaciones(espera=10):
                msg = u.get("message") or u.get("channel_post") or {}
                chat = msg.get("chat") or {}
                if chat.get("id"):
                    tg["chat_id"] = chat["id"]
                    nombre = chat.get("title") or chat.get("first_name") or "?"
                    log(f"  Detectado: {nombre} (chat_id {chat['id']})")
                    break
            if tg.get("chat_id"):
                break
        else:
            log("  ! No llego ningun mensaje. Volve a correr el comando.")
            return None

    d["telegram"] = tg
    guardar_credenciales(raiz, d)
    Bot(tg["token"], tg["chat_id"]).mensaje(
        "<b>Buscador de propiedades conectado.</b>\n"
        "Desde ahora te aviso cuando aparezca algo que valga la pena.")
    log("  Listo: te mande un mensaje de prueba.")
    return tg


# ------------------------------------------------------------------ formato
def _fmt(n, pre=""):
    return f"{pre}{n:,.0f}".replace(",", ".") if n else "—"


# Algunos avisos traen la direccion con toda la cola de Google pegada:
# "Av. Cordoba 2647, C1046 Cdad. Autonoma de Buenos Aires, Argentina, ...".
# En el telefono eso ocupa tres renglones y no aporta nada.
_RUIDO = ("capital federal", "cdad. autonoma", "cdad. autónoma", "argentina",
          "buenos aires", "caba")


def direccion_corta(direccion, largo=52):
    if not direccion:
        return ""
    partes = []
    for parte in direccion.split(","):
        p = parte.strip()
        if not p or any(r in p.lower() for r in _RUIDO):
            break
        partes.append(p)
        if len(", ".join(partes)) > largo:
            break
    texto = ", ".join(partes) or direccion.split(",")[0].strip()
    return texto[:largo].rstrip(" ,.")


# Telegram permite 4096 caracteres en un mensaje pero solo 1024 en el pie de
# una foto. Se recorta sacando primero lo menos importante, no truncando el
# final, que dejaria una linea cortada al medio.
TOPE_PIE = 1024


def ficha(pub, res, fila, umbral, tope=None):
    """Un mensaje por propiedad, con lo justo para decidir en diez segundos."""
    lineas = [f"<b>{res.puntaje:.0f} pts · USD {_fmt(pub.precio_usd)}</b>"]

    detalle = [pub.barrio, pub.tipo]
    if pub.ambientes:
        detalle.append(f"{pub.ambientes} amb")
    if pub.m2:
        detalle.append(f"{pub.m2:.0f} m²")
    lineas.append(" · ".join(detalle))
    corta = direccion_corta(pub.direccion)
    if corta:
        lineas.append(f"<i>{corta}</i>")

    if res.rendimiento:
        alq = f" (alq. est. USD {_fmt(res.alquiler_estimado)}/mes)" if res.alquiler_estimado else ""
        lineas.append(f"📈 Rinde <b>{res.rendimiento*100:.1f}%</b> bruto{alq}")
    if pub.expensas_ars:
        lineas.append(f"💸 Expensas ${_fmt(pub.expensas_ars)}")
    if pub.precio_m2:
        lineas.append(f"📐 USD {_fmt(pub.precio_m2)}/m²")

    # Lo que hace que valga la pena mirarlo, no el detalle tecnico.
    destacados = [m for m in res.motivos
                  if m.startswith(("BAJO DE PRECIO", "REVISAR", "cerca de",
                                   "expensas altas", "expensas bajas"))
                  or "vs mediana" in m or "vendedor cansado" in m]
    for m in destacados[:3]:
        lineas.append(f"• {m}")

    # Que haya varios en venta en el mismo edificio se dice ANTES de ir a
    # verlo: puede ser un edificio de inversores o un consorcio con problemas.
    total = fila.get("en_edificio_total") or 1
    if total > 1:
        donde = "en el edificio" if fila.get("edificio_exacto", 1) else "en la cuadra"
        aparte = ""
        if (fila.get("en_edificio") or 1) < total:
            aparte = f", {fila['en_edificio']} con puntaje"
        lineas.append(f"🏢 <i>{total} en venta {donde}{aparte}</i>")
    if fila.get("geo_dudosa"):
        lineas.append("📍 <i>verificar ubicación</i>")
    copias = fila.get("copias", 1)
    if copias > 1:
        lineas.append(f"<i>publicada {copias} veces "
                      f"({', '.join(fila.get('fuentes_copias') or [])})</i>")

    texto = "\n".join(lineas)
    if tope and len(texto) > tope:
        # Se van soltando los motivos (las lineas que empiezan con ·), que son
        # el detalle; nunca el precio, la ubicacion ni el rendimiento.
        esenciales = [l for l in lineas if not l.startswith("•")]
        detalles = [l for l in lineas if l.startswith("•")]
        while detalles and len("\n".join(lineas)) > tope:
            detalles.pop()
            lineas = esenciales[:4] + detalles + esenciales[4:]
        texto = "\n".join(lineas)[:tope]
    return texto


# Los callback_data de Telegram tienen un tope de 64 bytes, asi que las
# acciones van en una sola letra: i=interesa, n=no interesa, e=edificio entero.
ACCIONES = {"i": ("interesa", False), "n": ("no_interesa", False),
            "e": ("no_interesa", True), "c": ("caida", False)}


def botones(pub):
    """Marcar, no descartar: nada se pierde, todo se puede consultar despues.

    La excepcion es "Ya no esta", que es lo unico que saca la propiedad de
    circulacion. Va en su propia fila y al final: no es una opinion sobre la
    propiedad como los otros tres, es un hecho sobre el aviso.
    """
    return [
        [{"text": "Ver aviso", "url": pub.url}],
        [{"text": "★ Me interesa", "callback_data": f"i|{pub.clave}"[:64]},
         {"text": "✖ No me interesa", "callback_data": f"n|{pub.clave}"[:64]}],
        [{"text": "🏢 Todo el edificio, no", "callback_data": f"e|{pub.clave}"[:64]}],
        [{"text": "🗑 Ya no está", "callback_data": f"c|{pub.clave}"[:64]}],
    ]
