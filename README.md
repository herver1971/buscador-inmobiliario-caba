# Buscador de oportunidades inmobiliarias — CABA

Recorre los portales inmobiliarios (Zonaprop, MercadoLibre y Argenprop), puntúa
cada publicación **como inversión para renta** y te arma un informe con lo
mejor, ordenado. La idea no es "buscar más rápido" sino ver lo que los portales
no muestran: **el rendimiento estimado, las bajadas de precio y el precio por m²
comparado con el de la zona.**

Hace tres cosas:

1. **Recolecta** las publicaciones de venta y de alquiler y las guarda en una
   base de datos en tu compu (un solo archivo, SQLite).
2. **Puntúa** cada venta de 0 a 100 y arma un **informe HTML** y un **ranking**.
3. Te lo deja ver en una **web local** (listado con filtros + mapa), donde
   marcás qué te interesa y qué no. Opcionalmente te **avisa por Telegram**.

Todo corre en tu compu. No hace falta hosting, ni PHP, ni MySQL, ni cuenta en
ningún servicio. Zona por defecto: de Av. Rivadavia hacia el norte, en CABA (se
cambia desde `config/`).

> **Aviso.** Lee páginas públicas de los portales, despacio y de a una. Los
> portales pueden cambiar su HTML (y entonces una fuente deja de andar hasta que
> se arregle) o bloquear tu IP un rato si les pedís demasiado. Usalo con
> criterio y respetando sus condiciones de uso. El rendimiento que calcula es
> una **estimación bruta** para ordenar oportunidades, no un consejo financiero.

---

## 1. Qué necesitás

- **Python 3.10 o más nuevo.**
- Conexión a internet.
- Unos 200 MB libres. La primera corrida completa tarda **varias horas** (ver
  más abajo) y la compu tiene que quedar prendida.

### Instalar Python

**Windows**

- Bajalo de <https://www.python.org/downloads/> y, en la primera pantalla del
  instalador, **tildá "Add python.exe to PATH"** antes de apretar *Install Now*; o
- desde PowerShell: `winget install Python.Python.3.12`

Para comprobarlo, abrí **PowerShell** y escribí `py --version`. Tiene que decir
3.10 o más.

**Linux / macOS**

Casi seguro ya lo tenés: `python3 --version`. Si no, instalalo con el
gestor de tu sistema (`sudo apt install python3 python3-venv` en Debian/Ubuntu;
`brew install python` en macOS).

---

## 2. Instalación

### Descargar el proyecto

Cualquiera de las dos:

- con git: `git clone <URL-del-repositorio>` y entrá a la carpeta; o
- en la página del repositorio, **Code → Download ZIP**, descomprimilo y entrá a
  la carpeta.

### Crear el entorno e instalar las dependencias

Un "entorno virtual" es una carpeta (`.venv`) donde quedan instaladas las
librerías del proyecto sin ensuciar el resto de tu compu.

**Windows (PowerShell)**, parado en la carpeta del proyecto:

```powershell
py -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

Si al activar el entorno PowerShell dice que *"la ejecución de scripts está
deshabilitada en este sistema"*, corré esto una sola vez y repetí el paso:

```powershell
Set-ExecutionPolicy -Scope CurrentUser RemoteSigned
```

**Linux / macOS**

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

Cuando el entorno está activo ves `(.venv)` al principio de la línea. Si cerrás
la terminal, hay que volver a activarlo antes de usar el proyecto. De acá en
adelante los comandos son iguales en todos los sistemas; en Linux/macOS, si
`python` no existe, usá `python3`.

---

## 3. Primera prueba (unos minutos)

Antes de lanzar la corrida larga, probá con un solo barrio:

```
python -m inmobiliario recolectar --solo-barrio "Villa Urquiza" --limite 6
python -m inmobiliario informe
python -m inmobiliario estado
```

- `recolectar` recorre los portales para ese barrio y guarda lo que encuentra.
  Verás líneas como `Villa Urquiza  depto  venta -> 334`.
- `informe` puntúa todo y deja `salida/informe-AAAA-MM-DD.html`. **Abrí ese
  archivo con el navegador** (doble clic).
- `estado` te dice cuántas publicaciones hay en la base.

Es normal ver algún mensaje `parece bloqueo del portal` de Argenprop: es el más
delicado de los tres y la herramienta sigue con los otros.

Con una sola zona, los números de rendimiento todavía son poco fiables (faltan
alquileres para comparar). Mejoran con la corrida completa.

---

## 4. Corrida completa

```
python -m inmobiliario recolectar
python -m inmobiliario informe
```

**Cuánto tarda:** la primera vez, entre **2 y 6 horas** (los portales se piden
despacio a propósito; Argenprop espera 12 a 25 segundos entre pedidos porque si
no te bloquea). Las corridas siguientes son más cortas. **No cierres la terminal
ni dejes que la compu se suspenda** mientras corre. Si se corta, no se pierde
lo hecho: se guarda después de cada búsqueda, y podés volver a lanzarla.

Si querés guardar lo que va mostrando, **no uses `| tail` ni `| head`** con
`recolectar`: retienen toda la salida hasta el final y parece colgado. Usá:

- PowerShell: `python -m inmobiliario recolectar | Tee-Object corrida.log`
- Linux/macOS: `python -m inmobiliario recolectar | tee corrida.log`

### Por qué `recolectar` es rápido y `informe` no tanto

`recolectar` **no geocodifica**: solo ubica por barrio, que es gratis. `informe`
puntúa en dos pasadas: primero ordena sin coordenadas exactas y después **resuelve
la dirección solo de los mejores candidatos** (800 por defecto, ver
`geocodificar_top`), para aplicarles el bonus por cercanía a hospitales y
facultades. Geocodificar las 17.000 publicaciones para que 16.000 queden al fondo
del ranking es tiempo tirado. Las direcciones resueltas se guardan, así que ese
costo se paga una sola vez por dirección.

---

## 5. La web local

```
python -m inmobiliario web
```

Abrí <http://127.0.0.1:8765> en tu navegador (o agregá `--abrir` para que se
abra solo; `--puerto 9000` cambia el puerto). Para cerrarla, `Ctrl+C`.

- **Solo la ves vos, en tu compu.** Escucha únicamente en `127.0.0.1`, no en la
  red de tu casa, y no tiene contraseña porque no hace falta. **No hay que
  instalar hosting, PHP ni MySQL.**
- **Listado** con filtros (estado, barrio, tipo, ambientes, precio, rendimiento,
  texto) y orden por puntaje, rendimiento, precio, etc.
- **Mapa** (OpenStreetMap) con un pin por propiedad, color según el puntaje.
  Desde cada ficha hay un botón *📍 Mapa* y desde cada pin un *Ver ficha*. El
  mapa y las fotos se cargan de internet, así que necesitás conexión.
- **Marcar** cada propiedad: **★ Me interesa**, **No me interesa**, **🏢 Edificio,
  no** (descarta todos los avisos de ese edificio) y **Ya no está** (el aviso se
  cayó). Se puede dejar una nota con el motivo.
- Los datos que muestra son los del **último `informe`**: después de cada
  recolección hay que volver a correr `informe` para refrescarlos.
- Las marcas se guardan en la misma base que usa Telegram, así que las dos
  vistas ven siempre lo mismo.

Un resguardo: cualquier otra pestaña del navegador podría intentar "marcar por
vos" (un ataque conocido como CSRF). Por eso la web exige un token que genera
al arrancar y verifica de dónde viene cada pedido.

---

## 6. Telegram (opcional)

Te manda por Telegram las oportunidades nuevas que superen un puntaje, con foto
y botones para marcarlas desde el celular.

1. `python -m inmobiliario telegram` te guía: creás un bot hablando con
   **@BotFather** (comando `/newbot`), pegás el token que te da, y le mandás un
   mensaje cualquiera a tu bot para que detecte tu chat. El token queda en
   `config/credenciales.json`, que **no se sube a ningún lado** (está en
   `.gitignore`: no lo compartas).
2. `python -m inmobiliario avisar --simular` muestra en pantalla qué se mandaría,
   sin mandar nada. Sin `--simular` envía.
3. `python -m inmobiliario escuchar` deja corriendo el proceso que atiende los
   botones de los avisos (sin él los avisos llegan igual, pero los botones no
   hacen nada). Se deja abierto en una terminal.

**Criterio de aviso:**

- Nunca repite una publicación ya avisada.
- Una **bajada de precio avisa aunque no llegue al umbral**: es la señal más
  valiosa y no la publica ningún portal.
- Manda **un lote por corrida**, no un mensaje por publicación (tope en
  `notificacion.maximo_por_aviso`). Una herramienta que avisa de todo se
  silencia a los tres días.
- El umbral es `notificacion.umbral` (80 por defecto). Si preferís que avise de
  más, bajalo: es un número, no hay que tocar código.

**Los botones marcan, no borran.** *Me interesa* y *No me interesa* son tu
opinión: la propiedad sigue en la base y se puede consultar filtrando por estado.
Además de dejar de avisar, una marca **sigue a la propiedad** si reaparece
publicada por otra inmobiliaria o en otro portal (se compara por ubicación,
precio y ambientes, no solo por el número de aviso). *Todo el edificio, no*
aplica a todos los avisos de esa dirección. *Ya no está* (aviso caído) no es una
opinión sino un hecho, y saca la propiedad de circulación.

**Contestar un aviso con texto** guarda ese texto como nota de la propiedad (la
propiedad tiene que estar marcada primero).

Para deshacer una marca (por ejemplo un "ya no está" por error):

```
python -m inmobiliario desmarcar zonaprop:123456
```

---

## 7. Correrlo todos los días

La gracia de la herramienta crece con el tiempo: las **bajadas de precio** y los
avisos que llevan meses sin venderse solo se detectan con historia, y cada día
que no corre es historia que no se acumula. Conviene dejarla programada.

La corrida diaria encadena `recolectar → informe → avisar`; si un paso falla, los
siguientes no corren. Está en `extras/corrida-diaria.bat` (Windows) y
`extras/corrida-diaria.sh` (Linux/macOS), y deja el registro en
`salida/corrida.log`. Ambos usan el Python del entorno `.venv` del proyecto.

### Windows: Programador de tareas

La forma más rápida, en PowerShell (cambiá la ruta por la de tu carpeta; si tiene
espacios, dejá las comillas internas tal cual):

```powershell
schtasks /create /tn "Buscador inmobiliario" /sc daily /st 01:30 /tr "\"C:\Users\TuUsuario\buscador-inmobiliario-caba\extras\corrida-diaria.bat\""
```

Para probarla sin esperar a la madrugada: `schtasks /run /tn "Buscador inmobiliario"`.
Para quitarla: `schtasks /delete /tn "Buscador inmobiliario"`.

Para que además corra apenas prendas la compu si estaba apagada a esa hora, y sin
límite de tiempo, registrala así en lugar de lo anterior:

```powershell
$accion = New-ScheduledTaskAction -Execute "C:\Users\TuUsuario\buscador-inmobiliario-caba\extras\corrida-diaria.bat"
$disparador = New-ScheduledTaskTrigger -Daily -At 1:30am
$ajustes = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 12)
Register-ScheduledTask -TaskName "Buscador inmobiliario" -Action $accion -Trigger $disparador -Settings $ajustes
```

Paso a paso con la interfaz: abrí **Programador de tareas** → *Crear tarea
básica…* → nombre "Buscador inmobiliario" → *Diariamente* a las 01:30 →
*Iniciar un programa* → elegí `extras\corrida-diaria.bat` → *Finalizar*. Después,
doble clic en la tarea → pestaña *Configuración* → tildá *Ejecutar la tarea lo
antes posible si se omitió un inicio programado*.

Tené en cuenta que una compu **suspendida o apagada no ejecuta tareas**: dejala
prendida de noche o programá la tarea en un horario en que esté activa.

### Linux: timer de systemd de usuario

En `extras/` hay una unidad y un timer de ejemplo. Completá `RUTA_AL_PROYECTO`
con la carpeta del proyecto y:

```bash
mkdir -p ~/.config/systemd/user
cp extras/inmobiliario-corrida.service extras/inmobiliario-corrida.timer ~/.config/systemd/user/
nano ~/.config/systemd/user/inmobiliario-corrida.service     # completar la ruta
systemctl --user daemon-reload
systemctl --user enable --now inmobiliario-corrida.timer
systemctl --user list-timers 'inmobiliario*'                 # cuándo corre
systemctl --user start inmobiliario-corrida.service          # forzar una corrida ahora
```

`Persistent=true` hace que una corrida perdida (compu apagada) se dispare al
prender. Para que corra aunque no tengas la sesión abierta: `loginctl enable-linger $USER`.
Si usás Telegram, `extras/inmobiliario-bot.service` deja el proceso `escuchar`
permanente.

### Linux / macOS: cron

```
crontab -e
30 1 * * * /bin/sh /ruta/al/proyecto/extras/corrida-diaria.sh
```

(En macOS, `launchd` es lo más "oficial", pero cron funciona.)

---

## 8. Cómo ajustar la búsqueda

Todo se toca desde `config/`, con un editor de texto (el Bloc de notas sirve).
**Nunca hace falta editar código.**

| Para… | Archivo |
|---|---|
| Que un barrio pese más o menos, o sacarlo | `config/barrios.yaml` |
| Aceptar casa/PH donde hoy solo mira deptos | `config/barrios.yaml` (el peso es por barrio **y tipo**) |
| Sumar una facultad, hospital o proyecto que levante una zona | `config/pois.yaml` |
| Cambiar la franja de precio ideal | `config/config.yaml` → `precio` |
| Que el rendimiento pese más que el precio | `config/config.yaml` → `scoring.pesos` |
| Avisar de más o de menos por Telegram | `config/config.yaml` → `notificacion.umbral` |
| Sacar un aviso, una palabra o un edificio entero | `config/exclusiones.yaml` |
| Agregar un innegociable (cochera, antigüedad, expensas) | `config/config.yaml` → `filtros_duros` |
| Que la web muestre solo lo que pasa cierto puntaje | `config/config.yaml` → `salida.web_puntaje_minimo` |

Los valores por defecto están pensados para **inversión en renta con un
presupuesto de unos USD 50.000 a 70.000** (con un techo blando en 100.000) y
para departamentos y PH. Las casas vienen con peso 0 en todos los barrios porque
en esa franja casi no hay: es un dato, no un capricho (ver más abajo). Si tu
presupuesto o tu zona son otros, **empezá por `precio` y `barrios.yaml`**.

El principio de diseño es que **casi nada se descarta**: todo vive en el
puntaje. Ampliar la búsqueda es bajar un número, no descubrir que faltaba algo.

---

## 9. Cómo puntúa

```
mérito        = Σ (componente × peso)              0-100, mira la propiedad
peso_efectivo = min(10, peso_barrio_tipo + bonus_pois)
factor_zona   = (peso_efectivo / 10) ** exponente_zona
puntaje       = mérito × factor_zona               0-100, es lo que se ordena
```

Cada componente devuelve un valor **de 0 a 1** y se multiplica por su peso. Todo
sale de `inmobiliario/scoring.py` y se configura en `config/config.yaml` →
`scoring`.

| Componente | Peso | Qué mide |
|---|---|---|
| `rendimiento` | 30 | Alquiler anual estimado / precio |
| `precio` | 20 | Posición en la curva de precio |
| `precio_m2` | 15 | USD/m² contra la mediana del barrio |
| `señales` | 15 | Bajada de precio, antigüedad, tiempo publicado |
| `ambientes` | 10 | 1 y 2 ambientes rinden mejor |
| `expensas` | 10 | Carga de las expensas sobre la renta, ajustada por pares |

**`rendimiento`** (el más pesado): `bruto = alquiler_estimado × 12 / precio`. Con
`pobre: 0.04` y `excelente: 0.09`, 4 % anual o menos da 0 y 9 % o más da 1. El
alquiler estimado sale de los alquileres que la propia herramienta relevó, por
tres niveles, del más confiable al menos: (1) mediana de **barrio + tipo +
ambientes**; (2) **USD/m² de alquiler del barrio** × los m² de la propiedad; (3)
USD/m² global ajustado por el nivel de precios del barrio. Arriba de 20 % anual
el valor se marca `REVISAR`: casi siempre es una superficie o un precio mal
cargado, no una ganga. El rendimiento como alquiler temporario (tipo Airbnb) se
informa aparte y no entra en el puntaje.

**`precio`**: una curva de cinco tramos, no un techo seco. Vale 1 en la franja
ideal, sube linealmente hacia ella desde abajo, cae suavemente pasada una cota y
se apaga en `max_puntuable`. Se recolecta hasta `max_recoleccion` igual: algo caro
que rinda muy bien tiene que llegar, aunque sea al fondo de la lista.

**`precio_m2`**: `1,5 − (USD/m² propio / mediana del barrio y tipo)`. Estar en la
mediana da 0,5; un 33 % abajo, 0,83; sacar 1 exige estar a la mitad de la mediana.

**`señales`**: arranca en 0,5 y solo se mueve con tres cosas: una **bajada de
precio** de más de 3 % desde que la vemos (sube a 1), una antigüedad muy alta
(baja a 0,3) y llevar publicada más de 90 días (suma). Las dos señales que más
valen necesitan **historia**, así que este componente "se despierta" con los
meses de corridas diarias. Las bajadas no se detectan en emprendimientos: un
emprendimiento publica muchas unidades bajo un solo aviso y su precio salta solo.

**`ambientes`**: con `ambientes_ideales: [1, 2, 3]` el orden es la preferencia
(1 amb → 1,00 · 2 → 0,80 · 3 → 0,60; cualquier otra cosa 0,25).

**`expensas`**: mira cuánto se comen de la renta (hasta 15 % es cómodo, desde 35 %
ahogan) y las compara por m² contra edificios parecidos (mismo barrio, tipo y
tamaño). La carga manda y los pares la ajustan, sin promediarlas: que el barrio
entero sea caro no vuelve mejor negocio a un edificio caro. Hay una banda de
plausibilidad para descartar expensas de $1 o de $15 millones (datos basura).

**`factor_zona`**: `(peso_efectivo / 10) ** 0,6`. El exponente **comprime** la
diferencia entre zonas para que una buena oportunidad en un barrio mediano pueda
competir con una mediocre en uno top. Los **puntos de interés** (`pois.yaml`)
entran de este lado: un depto a 300 m de un hospital grande levanta el peso
efectivo de su barrio.

### Lo único que hace desaparecer una propiedad

Casi nada se descarta; los filtros duros son pocos:

| Filtro | Dónde se configura |
|---|---|
| **Precio por m² arriba de la mediana de su barrio y tipo** (con 5 % de tolerancia) | `config.yaml` → `filtros_duros.sobre_mediana_m2` (`0` lo apaga) |
| Precio arriba de `max_recoleccion` | `config.yaml` → `precio` |
| Peso 0 para ese tipo en ese barrio | `barrios.yaml` |
| Precio abajo de USD 5.000 (error de carga) | fijo en el código |
| Id, dirección o palabra excluida | `exclusiones.yaml` |
| Innegociables (cochera, antigüedad, expensas) | `config.yaml` → `filtros_duros` (vacío) |

Lo de la mediana contradice un poco el principio, y tiene su razón: pagar por m²
más que la mediana *de ese mismo barrio y tipo* no es una oferta sino una
propiedad cara en un barrio caro. Pero no se borra nada: **la publicación se sigue
relevando y su precio se sigue anotando**, y el día que baje de la mediana vuelve
a aparecer, con la bajada ya detectada. Lo que no se puede evaluar (sin m² propio
o sin mediana del barrio) no se descarta.

El ranking es uno solo, así que el tipo de mayor peso copa los primeros puestos. Por
eso el informe tiene la sección **"Lo mejor de cada tipo que no entró arriba"**.

### Sesgos conocidos del rendimiento estimado

El número es **optimista**, por tres razones que van en la misma dirección:
(1) de las ventas se piden los avisos más baratos primero y de los alquileres una
muestra representativa; (2) son precios de **publicación**, no de cierre; (3) es
rendimiento **bruto**: no descuenta ABL, seguro, vacancia, comisión ni
reparaciones. Sirve para **ordenar**; para decidir cuánto vas a ganar, restale
varios puntos.

### Cotas de sanidad

Entre los alquileres relevados se cuela basura (cocheras, "valor por noche",
precios de venta puestos como alquiler). `scoring.alquiler_plausible` corta lo
imposible (por defecto, menos de USD 150 o más de USD 20.000 al mes) sin tocar lo
caro pero real. Amoblados y temporarios se cuentan **aparte** de los vacíos
(alquilan 13 a 37 % más caro y mezclarlos inflaba las medianas).

---

## 10. Detalles que costaron descubrir

### El corte de Av. Rivadavia

Los portales filtran por barrio y Rivadavia parte varios al medio. Se resuelve en
tres niveles de certeza, y **nunca se descarta por duda**:

| Dato disponible | Qué hace |
|---|---|
| Dirección con altura | Geocodifica con USIG (oficial de la Ciudad) y compara contra la poligonal de Rivadavia |
| Solo barrio, entero al norte | Entra |
| Solo barrio, partido por Rivadavia | Entra marcado *verificar ubicación* |
| Inequívocamente al sur, o fuera de CABA | Se descarta |

La poligonal (`geo.py`) tiene precisión de ±150 m; la banda de duda (300 m por
defecto) absorbe ese error. MercadoLibre publica muchas direcciones como
`Bauness Al 2500`; USIG rechaza ese "Al", y sacarlo subió la cobertura de
coordenadas del 45 % al 82 %. Si algún día cae la cobertura, mirá primero
`Geocodificador._limpiar`. Para reintentar las que quedaron sin resolver:
`python -m inmobiliario geocodificar`.

### Las fuentes

| Fuente | Estado | Nota |
|---|---|---|
| **Zonaprop** | la mejor | Cada página trae un `window.__PRELOADED_STATE__` con coordenadas exactas, dirección con altura, expensas, antigüedad y superficies. |
| **MercadoLibre** | funciona | Se lee el HTML público. **Su API oficial no sirve** para esto: la búsqueda y las fichas de inmuebles de terceros devuelven 403 aun con OAuth completo. |
| **Argenprop** | frágil | Está detrás de CloudFront con control de bots y **bloquea por IP a los ~10 pedidos rápidos**; por eso espera 12 a 25 s (`fuentes.argenprop.espera_seg`; si te bloquea, subila: el bloqueo se levanta solo). |

Cada fuente es un módulo independiente en `inmobiliario/fuentes/`: si una queda
ciega, las demás siguen y el informe lo dice en la sección "Salud de las
fuentes". Los portales cambian su HTML cada tanto: **la fuente que más se rompe
es la que hay que mirar primero** si un día `recolectar` trae cero resultados.

Trampas ya resueltas, que conviene no reintroducir:

- **MercadoLibre** ignora `_Desde_N` salvo que se agregue `_NoIndex_True`; sin eso,
  paginar devuelve ocho veces la primera página sin ningún error.
- **Zonaprop**: el sufijo de precio es `-dolar`, no `-dolares` (con `-dolares`
  descarta el barrio y devuelve todo el país, sin error). El orden por precio
  ascendente pone primero los avisos "consultar precio", por eso se recorre por
  **bandas de precio**. El JSON se extrae con `json.JSONDecoder().raw_decode()`; un
  regex voraz se pasa de largo.
- **El precio tiene que ser el de la operación que se recolecta**: un aviso de
  "venta y alquiler" publica las dos, y quedarse con la primera guardaba un
  alquiler como precio de venta.
- Los sub-barrios de los portales ("Palermo Hollywood", "Once") se normalizan en
  `config.py` → `ALIAS`.

### Deduplicación entre portales

Dos pasadas, de la más segura a la más arriesgada. (1) Por **huella** (dirección
normalizada + superficie + ambientes), separando por expensas: dos avisos de la
misma dirección con expensas muy distintas son dos departamentos, no el mismo con
dos precios. (2) Por **cercanía geográfica**: dos avisos a menos de 60 m, mismo
precio, mismos ambientes y superficies compatibles (±15 %) son casi seguro el
mismo departamento; es lo único que funciona entre portales, donde la dirección
se escribe distinto. Al fusionar ganan los datos de Zonaprop. Nada se esconde: la
ficha muestra el contador de copias.

### Cuántos hay en venta en el mismo edificio

Tres o cuatro departamentos en venta en el mismo edificio es un dato (inversores,
un consorcio con problemas, una torre a estrenar). La ficha lo muestra con un chip
que lleva al listado de esa dirección, y dice los dos números cuando difieren:
"28 en venta en la cuadra, 2 con puntaje". Con coordenadas el grupo es el
edificio (🏢); sin ellas se agrupa por dirección redondeada a la centena, que es
media cuadra (📍), y el chip lo dice.

---

## 11. Estructura

```
config/           barrios.yaml, pois.yaml, config.yaml, exclusiones.yaml
                  credenciales.json  (Telegram; lo crea el comando; no se comparte)
data/             inmobiliario.db  (SQLite: el activo real del proyecto)
salida/           informes HTML y el registro de la corrida diaria
extras/           corrida diaria (.bat / .sh) y ejemplos de systemd
inmobiliario/
  cli.py          los comandos, el ranking y la fusión de duplicados
  config.py       carga de YAML, cotización del dólar, plan de búsqueda
  geo.py          corte de Rivadavia, geocodificación USIG, puntos de interés
  scoring.py      el motor de puntaje
  db.py           esquema, histórico de precios, medianas y marcas
  modelo.py       formato común al que normaliza cada portal
  informe.py      informe HTML
  ranking.py      deja el ranking puntuado en la base para la web
  web.py          la web local (solo biblioteca estándar)
  web_estatico/   estilos y JavaScript de la web
  telegram.py     avisos con foto y botones de marcar
  fuentes/        un módulo por portal
```

El flujo de cada día:

```
recolectar → informe → avisar
   ↓           ↓          ↓
 SQLite     puntaje    Telegram
              ↓
         web local (ranking en la misma base)
```

Comandos: `recolectar`, `informe`, `estado`, `web`, `telegram`, `avisar`,
`escuchar`, `desmarcar`, `geocodificar`. Todos aceptan `--help`, y todos aceptan
`--base RUTA` antes del nombre del comando (`python -m inmobiliario --base otra.db
estado`) para usar otra base de datos.

## Licencia

Software libre bajo la **GNU GPL v3** (ver el archivo `LICENSE`).
Copyright (C) 2026 herver1971.
