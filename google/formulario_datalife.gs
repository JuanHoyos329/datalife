/**
 * DataLife · Formulario de registro de personas
 * ------------------------------------------------------------------
 * Pega este archivo en el editor de Apps Script de la hoja de cálculo
 * "DataLife – Registro de personas" (Extensiones → Apps Script) y
 * ejecuta la función `crearFormularioDataLife` una sola vez.
 *
 * Qué hace:
 *   1. Crea el Google Form con todas las preguntas del censo.
 *   2. Lo conecta a ESTA hoja: cada respuesta llega como una fila nueva
 *      en la pestaña "Respuestas de formulario 1".
 *   3. Instala un activador "al enviar el formulario" que ENVÍA la hoja
 *      completa a DataLife y la app corre el ETL al instante. No necesita
 *      cuenta de servicio de Google: solo DATALIFE_WEBHOOK_URL y
 *      DATALIFE_WEBHOOK_TOKEN en Propiedades del script.
 *   4. Agrega el menú "DataLife" a la hoja: enviar ahora, cargar
 *      respuestas de prueba y ver el estado de la conexión.
 *
 * IMPORTANTE: el ETL de Python lee las columnas por el TÍTULO de cada
 * pregunta. Si cambias un título aquí, cámbialo también en
 * etl/etl_personas.py (diccionario COLUMNAS).
 */

var OPCIONES = {
  tipoDocumento: ['Cédula de ciudadanía', 'Tarjeta de identidad', 'Registro civil', 'Cédula de extranjería', 'PPT', 'Pasaporte', 'Sin documento'],
  tipoPersona: ['Residente', 'Trabajador', 'Visitante frecuente', 'Persona albergada'],
  grupo: ['Ninguno', 'Movilidad reducida', 'Adulto mayor', 'Menor de edad', 'Gestante'],
  torre: ['Torre A', 'Torre B', 'Torre C', 'Portería', 'Administración', 'Zonas comunes', 'No aplica'],
  punto: ['Zona verde norte', 'Parqueadero de visitantes'],
  albergue: ['No aplica', 'Albergue Piloto 01', 'Coliseo El Pueblo', 'Colegio Santa Librada']
};

function crearFormularioDataLife() {
  var hoja = SpreadsheetApp.getActiveSpreadsheet();
  var props = PropertiesService.getScriptProperties();
  if (props.getProperty('FORM_ID')) {
    var existente = FormApp.openById(props.getProperty('FORM_ID'));
    Logger.log('El formulario ya existe: ' + existente.getPublishedUrl());
    return;
  }

  var form = FormApp.create('DataLife · Registro de personas')
    .setDescription(
      'Censo para el plan de emergencias y la gestión de albergues. ' +
      'Tus datos se usan solo para atenderte en una emergencia (Ley 1581 de 2012). ' +
      'Los campos con * son obligatorios.')
    .setCollectEmail(false)
    .setLimitOneResponsePerUser(false)
    .setProgressBar(true)
    .setConfirmationMessage('¡Gracias! Tu registro quedó guardado. Te asignaremos un código P-0000 para el punto de encuentro.');

  // --- Sección 1: identificación ---
  form.addTextItem().setTitle('Nombre completo').setRequired(true);
  form.addListItem().setTitle('Tipo de documento').setChoiceValues(OPCIONES.tipoDocumento).setRequired(true);
  var doc = form.addTextItem().setTitle('Número de documento')
    .setHelpText('Solo números, sin puntos. Déjalo vacío si elegiste "Sin documento".');
  doc.setValidation(FormApp.createTextValidation()
    .setHelpText('Escribe solo números (5 a 15 dígitos).')
    .requireTextMatchesPattern('^[0-9]{5,15}$').build());
  form.addDateItem().setTitle('Fecha de nacimiento').setIncludesYear(true);

  // --- Sección 2: ubicación y prioridad ---
  form.addPageBreakItem().setTitle('Ubicación y atención prioritaria');
  form.addMultipleChoiceItem().setTitle('Tipo de persona').setChoiceValues(OPCIONES.tipoPersona).setRequired(true);
  form.addMultipleChoiceItem().setTitle('Grupo de atención prioritaria')
    .setHelpText('Nos ayuda a priorizar la evacuación y la atención en el albergue.')
    .setChoiceValues(OPCIONES.grupo).setRequired(true);
  form.addListItem().setTitle('Torre o área').setChoiceValues(OPCIONES.torre).setRequired(true);
  form.addTextItem().setTitle('Apartamento o cargo').setHelpText('Ej.: 302, o "Vigilancia" si eres trabajador.');
  form.addMultipleChoiceItem().setTitle('Punto de encuentro asignado').setChoiceValues(OPCIONES.punto).setRequired(true);

  // --- Sección 3: albergue ---
  form.addPageBreakItem().setTitle('Albergue (solo si estás en un albergue temporal)');
  form.addListItem().setTitle('Albergue').setChoiceValues(OPCIONES.albergue).setRequired(true);
  var integ = form.addTextItem().setTitle('Integrantes del grupo familiar')
    .setHelpText('¿Cuántas personas llegan contigo, contándote a ti? (1 a 20)');
  integ.setValidation(FormApp.createTextValidation()
    .setHelpText('Escribe un número entre 1 y 20.')
    .requireNumberBetween(1, 20).build());
  form.addParagraphTextItem().setTitle('Condiciones médicas o necesidades especiales')
    .setHelpText('Medicamentos permanentes, alergias, discapacidad… (opcional)');

  // --- Sección 4: contacto y autorización ---
  form.addPageBreakItem().setTitle('Contacto de emergencia y autorización');
  form.addTextItem().setTitle('Contacto de emergencia (nombre)');
  var cel = form.addTextItem().setTitle('Celular del contacto de emergencia');
  cel.setValidation(FormApp.createTextValidation()
    .setHelpText('Celular colombiano de 10 dígitos que empieza por 3.')
    .requireTextMatchesPattern('^3[0-9]{9}$').build());
  form.addCheckboxItem().setTitle('Autorización de tratamiento de datos (Ley 1581 de 2012)')
    .setHelpText('Autorizo a DataLife a usar mis datos solo para el plan de emergencias y la atención en albergues. ' +
                 'Puedo pedir que se corrijan o eliminen en cualquier momento.')
    .setChoiceValues(['Sí, autorizo el tratamiento de mis datos'])
    .setRequired(true);

  // Respuestas → esta hoja
  form.setDestination(FormApp.DestinationType.SPREADSHEET, hoja.getId());
  props.setProperty('FORM_ID', form.getId());

  instalarActivador_();

  Logger.log('Formulario creado.');
  Logger.log('Enlace para compartir (llenar): ' + form.getPublishedUrl());
  Logger.log('Enlace para editar:            ' + form.getEditUrl());
  Logger.log('Hoja de respuestas:            ' + hoja.getUrl());
}

/** Instala (una vez) el activador que corre al enviar el formulario. */
function instalarActivador_() {
  var hoja = SpreadsheetApp.getActiveSpreadsheet();
  var ya = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'alEnviarFormulario'; });
  if (!ya) ScriptApp.newTrigger('alEnviarFormulario').forSpreadsheet(hoja).onFormSubmit().create();
}

/**
 * ETL cada 15 minutos SIN GitHub Actions (gratis, corre en Google).
 * Ejecútala una vez a mano después de configurar las propiedades del script.
 * Sirve también para que Render no se duerma y Supabase no se pause.
 */
function instalarEtlCada15Min() {
  var ya = ScriptApp.getProjectTriggers().some(function (t) { return t.getHandlerFunction() === 'etlProgramado'; });
  if (!ya) ScriptApp.newTrigger('etlProgramado').timeBased().everyMinutes(15).create();
  Logger.log('Listo: el ETL se pedirá cada 15 minutos.');
}

function quitarEtlCada15Min() {
  ScriptApp.getProjectTriggers().forEach(function (t) {
    if (t.getHandlerFunction() === 'etlProgramado') ScriptApp.deleteTrigger(t);
  });
  Logger.log('ETL programado desactivado.');
}

function etlProgramado() {
  alEnviarFormulario(null);
}

/** Menú "DataLife" en la hoja de cálculo. */
function onOpen() {
  SpreadsheetApp.getUi().createMenu('DataLife')
    .addItem('Enviar respuestas a DataLife ahora', 'enviarAhora')
    .addItem('Agregar 10 respuestas de prueba', 'enviarRespuestasDePrueba')
    .addSeparator()
    .addItem('Probar conexión con DataLife', 'probarConexion')
    .addToUi();
}

/** Busca la pestaña de respuestas (la que tiene la columna "Nombre completo"). */
function hojaRespuestas_() {
  var libro = SpreadsheetApp.getActiveSpreadsheet();
  var hojas = libro.getSheets();
  for (var i = 0; i < hojas.length; i++) {
    var h = hojas[i];
    if (h.getLastRow() >= 1 && h.getRange(1, 1, 1, h.getLastColumn()).getDisplayValues()[0].indexOf('Nombre completo') >= 0) return h;
  }
  return null;
}

/**
 * Envía TODA la pestaña de respuestas a DataLife (POST /api/etl/ejecutar).
 * El ETL es idempotente: las personas ya cargadas se actualizan, no se duplican.
 * Devuelve el texto de la respuesta de la app.
 */
function enviarHoja_() {
  var props = PropertiesService.getScriptProperties();
  var url = props.getProperty('DATALIFE_WEBHOOK_URL');
  var token = props.getProperty('DATALIFE_WEBHOOK_TOKEN');
  if (!url || !token) throw new Error('Faltan DATALIFE_WEBHOOK_URL y DATALIFE_WEBHOOK_TOKEN en Propiedades del script.');
  var hoja = hojaRespuestas_();
  if (!hoja) throw new Error('No encontré la pestaña de respuestas. Ejecuta primero crearFormularioDataLife.');
  var valores = hoja.getDataRange().getDisplayValues();   // fechas tal como se ven: dd/mm/aaaa
  var r = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: { 'X-DataLife-Token': token },
    payload: JSON.stringify({ origen: 'google_forms', hoja: hoja.getName(), valores: valores }),
    muteHttpExceptions: true
  });
  return r.getResponseCode() + ' ' + r.getContentText();
}

/** Menú → "Enviar respuestas a DataLife ahora". */
function enviarAhora() {
  var msg;
  try { msg = enviarHoja_(); } catch (err) { msg = 'Error: ' + err.message; }
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('DataLife', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) { /* sin interfaz */ }
}

/** Menú → "Probar conexión con DataLife": llama /salud. */
function probarConexion() {
  var url = PropertiesService.getScriptProperties().getProperty('DATALIFE_WEBHOOK_URL') || '';
  var base = url.replace(/\/api\/etl\/ejecutar\/?$/, '');
  var msg = base ? UrlFetchApp.fetch(base + '/salud', { muteHttpExceptions: true }).getContentText()
                 : 'Falta DATALIFE_WEBHOOK_URL en Propiedades del script.';
  Logger.log(msg);
  try { SpreadsheetApp.getUi().alert('DataLife', msg, SpreadsheetApp.getUi().ButtonSet.OK); } catch (e) { /* sin interfaz */ }
}

/**
 * Llena el formulario 10 veces con personas FICTICIAS (como si fueran
 * personas reales respondiendo). Sirve para probar todo el camino:
 * Form → Hoja → DataLife. Los documentos empiezan por 99 para reconocerlos.
 */
function enviarRespuestasDePrueba() {
  var formId = PropertiesService.getScriptProperties().getProperty('FORM_ID');
  if (!formId) throw new Error('Primero ejecuta crearFormularioDataLife.');
  var form = FormApp.openById(formId);
  var items = {};
  form.getItems().forEach(function (it) { items[it.getTitle()] = it; });

  var nombres = ['Ana Lucía Mosquera', 'Luis Fernando Arboleda', 'Sandra Milena Valencia', 'Jhon Jairo Caicedo',
                 'María José Riascos', 'Andrés Felipe Rengifo', 'Luz Dary Hurtado', 'Óscar Iván Quiñones',
                 'Paula Andrea Lozano', 'Wilmer Angulo Rivas'];
  var hoy = new Date();
  for (var i = 0; i < nombres.length; i++) {
    var albergada = i % 3 === 0;
    var resp = form.createResponse();
    var texto = function (t, v) { if (items[t] && v !== '') resp.withItemResponse(items[t].asTextItem().createResponse(String(v))); };
    var lista = function (t, v) { if (items[t]) resp.withItemResponse(items[t].asListItem().createResponse(v)); };
    var opcion = function (t, v) { if (items[t]) resp.withItemResponse(items[t].asMultipleChoiceItem().createResponse(v)); };
    texto('Nombre completo', nombres[i]);
    lista('Tipo de documento', 'Cédula de ciudadanía');
    texto('Número de documento', '99' + String(10000000 + Math.floor(Math.random() * 89999999)));
    if (items['Fecha de nacimiento']) {
      resp.withItemResponse(items['Fecha de nacimiento'].asDateItem().createResponse(new Date(1950 + i * 6, i, 10 + i)));
    }
    opcion('Tipo de persona', albergada ? 'Persona albergada' : (i % 3 === 1 ? 'Residente' : 'Trabajador'));
    opcion('Grupo de atención prioritaria', OPCIONES.grupo[i % OPCIONES.grupo.length]);
    lista('Torre o área', OPCIONES.torre[i % 3]);
    texto('Apartamento o cargo', albergada ? '' : String(101 + i * 10));
    opcion('Punto de encuentro asignado', OPCIONES.punto[i % 2]);
    lista('Albergue', albergada ? OPCIONES.albergue[1 + (i % 3)] : 'No aplica');
    texto('Integrantes del grupo familiar', albergada ? String(2 + (i % 4)) : '');
    if (items['Condiciones médicas o necesidades especiales'] && i % 4 === 0) {
      resp.withItemResponse(items['Condiciones médicas o necesidades especiales'].asParagraphTextItem().createResponse('Hipertensión'));
    }
    texto('Contacto de emergencia (nombre)', 'Familiar de ' + nombres[i].split(' ')[0]);
    texto('Celular del contacto de emergencia', '31' + String(10000000 + Math.floor(Math.random() * 89999999)));
    var aut = items['Autorización de tratamiento de datos (Ley 1581 de 2012)'];
    if (aut) resp.withItemResponse(aut.asCheckboxItem().createResponse(['Sí, autorizo el tratamiento de mis datos']));
    resp.submit();
  }
  SpreadsheetApp.flush();
  Logger.log('Listo: 10 respuestas de prueba enviadas (' + hoy.toLocaleString() + ').');
  enviarAhora();
}

/**
 * Se ejecuta en cada respuesta nueva: envía la hoja a DataLife y la app corre el ETL.
 * Configura en Apps Script → Configuración del proyecto → Propiedades del script:
 *   DATALIFE_WEBHOOK_URL   = https://TU-APP.onrender.com/api/etl/ejecutar
 *   DATALIFE_WEBHOOK_TOKEN = el mismo valor de ETL_WEBHOOK_TOKEN en la app
 * Si no están configuradas, no pasa nada (puedes subir el .csv desde la app).
 */
function alEnviarFormulario(e) {
  var props = PropertiesService.getScriptProperties();
  if (!props.getProperty('DATALIFE_WEBHOOK_URL') || !props.getProperty('DATALIFE_WEBHOOK_TOKEN')) return;
  try {
    Logger.log(enviarHoja_());
  } catch (err) {
    console.error('No se pudo avisar a DataLife: ' + err);
  }
}
