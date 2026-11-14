// Cliente de la API de especies. Todo el cálculo lo hace Python (api.py + especies.py);
// el navegador sólo pide los datos con fetch() y los dibuja.
// Si la web se sirve desde otro dominio, poner aquí la URL de la API ("https://api.midominio.com").
const API = "";

const MESES = ["Enero", "Febrero", "Marzo", "Abril", "Mayo", "Junio", "Julio", "Agosto",
               "Septiembre", "Octubre", "Noviembre", "Diciembre"];
const NOMBRES_VARIABLE = {
  temperatura: "Temperatura", salinidad: "Salinidad", fitoplancton: "Clorofila",
  fluorescencia: "Fluorescencia", dif_temp_mar_aire: "Dif. temp. mar-aire",
  evaporacion: "Evaporación", viento_velocidad: "Viento",
};
// Recorte del mapa (igual que la rejilla del backend).
const LON_MIN = -6, LON_MAX = 36.5, LAT_MIN = 30, LAT_MAX = 46, PASO = 0.5;

const $ = (id) => document.getElementById(id);
let especieActual = null;
let mapaActual = null;

async function api(ruta) {
  const r = await fetch(API + ruta);
  if (!r.ok) {
    const cuerpo = await r.json().catch(() => ({}));
    throw new Error(cuerpo.detail || `Error ${r.status}`);
  }
  return r.json();
}

// ---------------------------------------------------------------- lista y búsqueda

async function cargarLista(seleccionar) {
  const lista = await api("/api/especies");
  $("lista").innerHTML = lista.map((e) =>
    `<option value="${e.nombre}">${e.nombre}${e.nombre_comun ? " · " + e.nombre_comun : ""}</option>`).join("");
  if (seleccionar) $("lista").value = seleccionar;
  return lista;
}

let temporizador;
$("buscar").addEventListener("input", (ev) => {
  clearTimeout(temporizador);
  const q = ev.target.value.trim();
  if (q.length < 3) return;
  temporizador = setTimeout(async () => {
    try {
      const res = await api(`/api/especies/buscar?q=${encodeURIComponent(q)}`);
      $("sugerencias").innerHTML = res.map((e) =>
        `<option value="${e.nombre}">${e.clase || ""} ${e.familia ? "· " + e.familia : ""}</option>`).join("");
    } catch { /* la búsqueda es opcional */ }
  }, 300);
});
$("buscar").addEventListener("change", (ev) => {
  const nombre = ev.target.value.trim();
  if (nombre.split(" ").length >= 2) mostrarEspecie(nombre);
});
$("lista").addEventListener("change", (ev) => mostrarEspecie(ev.target.value));
$("mes").addEventListener("input", () => {
  $("mesTexto").textContent = MESES[$("mes").value - 1];
  if (especieActual) cargarMapa();
});

// ---------------------------------------------------------------- especie

async function mostrarEspecie(nombre) {
  especieActual = nombre;
  $("estado").textContent = "Calculando… (la primera vez que se pide una especie puede tardar unos segundos)";
  try {
    // Si la especie no estaba calculada, Python la calcula ahora y la guarda en la BD.
    const datos = await api(`/api/especies/${encodeURIComponent(nombre)}`);
    if (especieActual !== nombre) return;  // el usuario ya eligió otra
    $("titulo").textContent = `${datos.nombre}${datos.nombre_comun ? " · " + datos.nombre_comun : ""}`;
    $("estado").textContent = `${datos.registros_usados} avistamientos usados de ${datos.registros} ` +
      `(desde 2000). Calculado: ${new Date(datos.calculado).toLocaleString()}`;
    pintarResumen(datos);
    await cargarLista(datos.nombre);
    await cargarMapa();
  } catch (e) {
    $("estado").textContent = `No se pudo calcular ${nombre}: ${e.message}`;
  }
}

function pintarResumen(datos) {
  const pct = datos.resumen.superficie_favorable_pct;
  $("meses").innerHTML = MESES.map((m, i) => {
    const v = pct[i + 1] ?? 0;
    return `<div style="height:${Math.max(2, v)}%" title="${m}: ${v}%"></div>`;
  }).join("");

  const lim = Object.entries(datos.resumen.factores_limitantes_pct);
  $("limitantes").innerHTML = lim.length
    ? lim.map(([v, p]) => `<li>${NOMBRES_VARIABLE[v] || v}: ${p}% de las zonas no favorables</li>`).join("")
    : "<li>Sin zonas desfavorables</li>";

  const fmt = (x) => (Math.abs(x) >= 100 ? x.toFixed(0) : Math.abs(x) >= 1 ? x.toFixed(2) : x.toPrecision(3));
  $("nicho").innerHTML =
    "<tr><th>Variable</th><th>p5</th><th>p25</th><th>Mediana</th><th>p75</th><th>p95</th><th>Unidades</th></tr>" +
    Object.entries(datos.nicho).map(([v, n]) =>
      `<tr><td>${NOMBRES_VARIABLE[v] || v}</td><td>${fmt(n.p5)}</td><td>${fmt(n.p25)}</td>` +
      `<td><strong>${fmt(n.p50)}</strong></td><td>${fmt(n.p75)}</td><td>${fmt(n.p95)}</td>` +
      `<td>${n.unidades}</td></tr>`).join("");
}

// ---------------------------------------------------------------- mapa

async function cargarMapa() {
  const nombre = especieActual, mes = $("mes").value;
  const datos = await api(`/api/especies/${encodeURIComponent(nombre)}/mapa?mes=${mes}`);
  if (especieActual !== nombre || $("mes").value !== mes) return;
  mapaActual = datos;
  dibujarMapa();
}

const lienzo = $("mapa");
const ctx = lienzo.getContext("2d");
const aX = (lon) => ((lon - LON_MIN) / (LON_MAX - LON_MIN)) * lienzo.width;
const aY = (lat) => ((LAT_MAX - lat) / (LAT_MAX - LAT_MIN)) * lienzo.height;

function color(v) {
  // Escala secuencial de claro (0) a oscuro (1).
  const tramos = [[217, 238, 243], [108, 184, 204], [11, 110, 138], [7, 59, 76]];
  const x = Math.min(0.999, Math.max(0, v)) * (tramos.length - 1);
  const i = Math.floor(x), f = x - i, a = tramos[i], b = tramos[i + 1];
  return `rgb(${a.map((c, k) => Math.round(c + (b[k] - c) * f)).join(",")})`;
}

function dibujarMapa() {
  const estilos = getComputedStyle(document.documentElement);
  ctx.fillStyle = estilos.getPropertyValue("--tierra");
  ctx.fillRect(0, 0, lienzo.width, lienzo.height);
  if (!mapaActual) return;
  const w = aX(PASO) - aX(0), h = aY(0) - aY(PASO);
  for (const [lat, lon, v] of mapaActual.puntos) {
    ctx.fillStyle = color(v);
    ctx.fillRect(aX(lon - PASO / 2), aY(lat + PASO / 2), w + 0.5, h + 0.5);
  }
  ctx.fillStyle = estilos.getPropertyValue("--punto");
  for (const [lat, lon, n] of mapaActual.avistamientos) {
    ctx.beginPath();
    ctx.arc(aX(lon), aY(lat), Math.min(6, 1.5 + Math.log10(n + 1) * 2), 0, 2 * Math.PI);
    ctx.fill();
  }
}

lienzo.addEventListener("mousemove", (ev) => {
  if (!mapaActual) return;
  const r = lienzo.getBoundingClientRect();
  const lon = LON_MIN + ((ev.clientX - r.left) / r.width) * (LON_MAX - LON_MIN);
  const lat = LAT_MAX - ((ev.clientY - r.top) / r.height) * (LAT_MAX - LAT_MIN);
  const p = mapaActual.puntos.find(([la, lo]) => Math.abs(la - lat) <= PASO / 2 && Math.abs(lo - lon) <= PASO / 2);
  $("info").textContent = p
    ? `${p[0]}° N, ${p[1]}° E · idoneidad ${p[2].toFixed(2)}` +
      (p[2] < mapaActual.umbral_idoneo && p[3] ? ` · limitada por ${NOMBRES_VARIABLE[p[3]] || p[3]}` : "")
    : "";
});

// ---------------------------------------------------------------- inicio

(async function iniciar() {
  document.querySelector(".meses-et").innerHTML = MESES.map((m) => `<span>${m[0]}</span>`).join("");
  $("mes").value = new Date().getMonth() + 1;
  $("mesTexto").textContent = MESES[$("mes").value - 1];
  dibujarMapa();
  try {
    const lista = await cargarLista();
    if (lista.length) mostrarEspecie(lista[0].nombre);
    else $("estado").textContent = "Aún no hay especies calculadas: busca una por su nombre científico.";
  } catch (e) {
    $("estado").textContent = `No se puede conectar con la API: ${e.message}`;
  }
})();
