// Listado: marcar propiedades (me interesa / no / todo el edificio / ya no esta).
// Todo lo que hay que preguntar se pregunta DENTRO de la ficha, sin prompt() ni
// confirm(): esos tapan la pantalla y no muestran la propiedad que uno decide.
const CSRF = document.querySelector('meta[name="csrf"]').content;

function avisarError(texto) {
  let a = document.getElementById('aviso');
  if (!a) {
    a = document.createElement('div');
    a.id = 'aviso';
    document.body.appendChild(a);
  }
  a.textContent = texto;
  a.style.display = 'block';
  setTimeout(() => { a.style.display = 'none'; }, 5000);
}

// El <details> viene abierto en el HTML a proposito: si esto no corre, los
// filtros se ven. En el celular se pliegan, salvo que haya alguno puesto.
const cajon = document.querySelector('.cajon');
if (cajon && window.matchMedia('(max-width:700px)').matches
    && !cajon.querySelector('summary').textContent.includes(':')) {
  cajon.open = false;
}

async function guardar(ficha, cuerpo) {
  const r = await fetch('/marcar', {
    method: 'POST',
    headers: {'Content-Type': 'application/json', 'X-CSRF': CSRF},
    body: JSON.stringify(Object.assign({clave: ficha.dataset.clave}, cuerpo)),
  });
  const d = await r.json();
  if (!d.ok) throw new Error(d.error || 'No se pudo guardar');
  return d;
}

function cerrarCuadro() {
  document.querySelectorAll('.anotador').forEach(a => a.remove());
  document.querySelectorAll('.acciones button[disabled]')
          .forEach(b => { b.disabled = false; });
}

function cuadro(ficha, opciones) {
  cerrarCuadro();
  const caja = document.createElement('div');
  caja.className = 'anotador';

  if (opciones.mensaje) {
    const dice = document.createElement('p');
    dice.className = 'dice';
    dice.textContent = opciones.mensaje;
    caja.appendChild(dice);
  }

  let texto = null;
  if (opciones.nota !== undefined) {
    texto = document.createElement('textarea');
    texto.rows = 2;
    texto.maxLength = 500;
    texto.placeholder = '¿Por qué? (opcional)';
    texto.value = opciones.nota || '';
    caja.appendChild(texto);
  }

  const ok = document.createElement('button');
  ok.className = opciones.clase || 'ok';
  ok.textContent = opciones.etiqueta;
  const cancelar = document.createElement('button');
  cancelar.className = 'sec';
  cancelar.textContent = 'Cancelar';

  const botonera = document.createElement('div');
  botonera.className = 'botonera';
  botonera.append(ok, cancelar);
  if (opciones.ayuda) {
    const ayuda = document.createElement('span');
    ayuda.className = 'ayuda';
    ayuda.textContent = opciones.ayuda;
    botonera.appendChild(ayuda);
  }
  caja.appendChild(botonera);
  ficha.appendChild(caja);
  (texto || ok).focus();

  const confirmar = async () => {
    ok.disabled = cancelar.disabled = true;
    try {
      await guardar(ficha, opciones.cuerpo(texto ? texto.value.trim() : null));
      location.reload();      // para que la ficha respete el filtro activo
    } catch (e) {
      ok.disabled = cancelar.disabled = false;
      avisarError('No se pudo guardar: ' + e.message);
    }
  };
  ok.addEventListener('click', confirmar);
  cancelar.addEventListener('click', cerrarCuadro);
  caja.addEventListener('keydown', (ev) => {
    if (ev.key === 'Escape') cerrarCuadro();
    // Enter guarda; Shift+Enter hace un salto de linea.
    if (ev.key === 'Enter' && !ev.shiftKey && ev.target === texto) {
      ev.preventDefault();
      confirmar();
    }
  });
}

document.addEventListener('click', (ev) => {
  const boton = ev.target.closest('.acciones button');
  if (!boton) return;
  const ficha = boton.closest('.ficha');
  const estado = boton.dataset.estado;

  if (boton.classList.contains('anotar')) {
    return cuadro(ficha, {
      nota: boton.dataset.nota, etiqueta: 'Guardar nota',
      ayuda: 'Vacío borra la nota',
      cuerpo: (nota) => ({estado: boton.dataset.marca, nota: nota}),
    });
  }
  if (boton.dataset.edificio) {
    return cuadro(ficha, {
      mensaje: 'Se marcan como «no me interesa» todos los avisos de este '
             + 'edificio, y también los que aparezcan después en esa dirección.',
      nota: '', etiqueta: '🏢 Todo el edificio, no',
      cuerpo: (nota) => ({estado: 'no_interesa', edificio: true, nota: nota}),
    });
  }
  if (estado === 'interesa' || estado === 'no_interesa') {
    return cuadro(ficha, {
      nota: '', ayuda: 'Sin escribir nada también vale',
      etiqueta: estado === 'interesa' ? '★ Me interesa' : '✖ No',
      cuerpo: (nota) => ({estado: estado, nota: nota}),
    });
  }
  // Es la unica accion que borra algo de la lista: se pregunta antes.
  if (estado === 'caida') {
    return cuadro(ficha, {
      mensaje: 'El aviso ya no existe. Sale del listado y la recolección '
             + 'deja de traerlo (se deshace con: python -m inmobiliario desmarcar CLAVE).',
      etiqueta: '🗑 Sí, ya no está', clase: 'peligro',
      cuerpo: () => ({estado: 'caida'}),
    });
  }

  // Quitar la marca no pregunta nada: se deshace apretando de nuevo.
  cerrarCuadro();
  boton.disabled = true;
  guardar(ficha, {estado: estado})
    .then(() => location.reload())
    .catch((e) => {
      boton.disabled = false;
      avisarError('No se pudo guardar la marca: ' + e.message);
    });
});
