// Mapa de las propiedades sobre OpenStreetMap, con Leaflet (cargado desde CDN).
// Los datos llegan como JSON en <script id="datos">; nada se arma con
// innerHTML a partir de texto de los portales.
const DATOS = JSON.parse(document.getElementById('datos').textContent);
const PUNTOS = DATOS.puntos;
const FOCO = DATOS.foco;

// Plegar ANTES de crear el mapa: Leaflet mide el contenedor una sola vez.
const cajon = document.querySelector('.cajon');
if (cajon && window.matchMedia('(max-width:700px)').matches
    && !cajon.querySelector('summary').textContent.includes(':')) {
  cajon.open = false;
}

const mapa = L.map('mapa').setView([-34.588, -58.44], 12);
L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
  maxZoom: 19,
  // La página va con no-referrer y OSM rechaza con 403 las teselas sin Referer
  // (su política de uso): solo a ellas se les manda el origen.
  referrerPolicy: 'strict-origin-when-cross-origin',
  attribution: '&copy; colaboradores de <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a>'
}).addTo(mapa);

const color = p => p >= 80 ? '#1b6b4c' : p >= 65 ? '#c99a2e' : '#7b8a99';

// Un pin con punta y borde blanco en vez de un punto: sobre las teselas de OSM
// un circulito de 6 px se pierde, y la punta dice en que esquina cae. El
// tamano sube con el puntaje; el que se vino a ver es mas grande y late.
const pin = (puntaje, foco) => {
  const ancho = foco ? 34 : puntaje >= 80 ? 27 : 21;
  const alto = Math.round(ancho * 1.45);
  return L.divIcon({
    className: 'pin' + (foco ? ' pin-foco' : ''),
    html: `<svg width="${ancho}" height="${alto}" viewBox="0 0 24 35">`
        + `<path d="M12 34S23 20 23 12A11 11 0 1 0 1 12c0 8 11 22 11 22z"`
        + ` fill="${color(puntaje)}" stroke="#fff" stroke-width="2.2"/>`
        + `<circle cx="12" cy="12" r="4" fill="#fff" fill-opacity=".92"/></svg>`,
    iconSize: [ancho, alto],
    iconAnchor: [ancho / 2, alto],
    popupAnchor: [0, -alto + 3],
  });
};
const nro = n => n == null ? '—' : n.toLocaleString('es-AR', {maximumFractionDigits: 0});

function elemento(tag, props, ...hijos) {
  const el = document.createElement(tag);
  Object.assign(el, props || {});
  el.append(...hijos);
  return el;
}

function globo(p) {
  const alq = p.alquiler_estimado ? ` (USD ${nro(+p.alquiler_estimado)}/mes)` : '';
  const rinde = p.rendimiento ? ` · rinde ${(p.rendimiento * 100).toFixed(1)}%${alq}` : '';
  const sup = p.m2 ? ` · ${nro(p.m2)} m²` : '';
  const amb = p.ambientes ? ` · ${p.ambientes} amb` : '';
  const enlaces = elemento('div', {className: 'enlaces'},
    elemento('a', {href: '/?clave=' + encodeURIComponent(p.clave), textContent: 'Ver ficha'}));
  if (/^https?:\/\//.test(p.url || '')) {
    enlaces.append(elemento('a', {href: p.url, target: '_blank', rel: 'noopener noreferrer',
                                  textContent: 'Ver aviso ↗'}));
  }
  return elemento('div', {className: 'pop'},
    elemento('b', {textContent: 'USD ' + nro(p.precio_usd)}),
    elemento('div', {className: 'd', textContent: `${p.direccion || ''} — ${p.barrio || ''}${amb}${sup}`}),
    elemento('div', {className: 'd', textContent: `${nro(p.puntaje)} puntos${rinde}`}),
    enlaces);
}

let enfocado = null;
const grupo = L.markerClusterGroup({maxClusterRadius: 45, chunkedLoading: true});
for (const p of PUNTOS) {
  const esFoco = p.clave === FOCO;
  const m = L.marker([p.lat, p.lon], {
    icon: pin(p.puntaje, esFoco),
    zIndexOffset: esFoco ? 1000 : 0,       // el que se vino a ver va por encima
  });
  m.bindPopup(globo(p));
  if (esFoco) enfocado = m;
  grupo.addLayer(m);
}
mapa.addLayer(grupo);

if (enfocado) {
  // zoomToShowLayer abre el cluster que la contiene; sin eso quedaria escondida
  // adentro de un circulo con un numero.
  grupo.zoomToShowLayer(enfocado, () => {
    // El centrado va DESPUES de abrir el globo (con 'once'): al abrirse,
    // Leaflet corre el mapa solo para que el globo entre en pantalla y eso lo
    // descentra; recentrar antes no sirve porque lo pisa.
    enfocado.once('popupopen', () => {
      mapa.setView(enfocado.getLatLng(), 17, {animate: false});
    });
    enfocado.openPopup();
  });
  // En el celular el mapa arranca debajo de la barra y los filtros.
  document.getElementById('mapa').scrollIntoView({block: 'center', behavior: 'smooth'});
} else if (PUNTOS.length) {
  mapa.fitBounds(grupo.getBounds().pad(0.05));
}

// Plegar o desplegar los filtros mueve el mapa y Leaflet no se entera solo.
if (cajon) cajon.addEventListener('toggle', () => mapa.invalidateSize());
