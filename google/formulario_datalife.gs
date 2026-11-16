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
 *   3. Instala un activador "al enviar el formulario" que avisa a la app
 *      de DataLife para que corra el ETL al instante (opcional: solo si
 *      configuras DATALIFE_WEBHOOK_URL y DATALIFE_WEBHOOK_TOKEN).
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

/**
 * Se ejecuta en cada respuesta nueva. Avisa a la app para que corra el ETL.
 * Configura en Apps Script → Configuración del proyecto → Propiedades del script:
 *   DATALIFE_WEBHOOK_URL   = https://TU-APP.onrender.com/api/etl/ejecutar
 *   DATALIFE_WEBHOOK_TOKEN = el mismo valor de ETL_WEBHOOK_TOKEN en la app
 * Si no están configuradas, no pasa nada: el ETL programado (GitHub Actions)
 * recogerá la respuesta en la siguiente ejecución.
 */
function alEnviarFormulario(e) {
  var props = PropertiesService.getScriptProperties();
  var url = props.getProperty('DATALIFE_WEBHOOK_URL');
  var token = props.getProperty('DATALIFE_WEBHOOK_TOKEN');
  if (!url || !token) return;
  try {
    UrlFetchApp.fetch(url, {
      method: 'post',
      contentType: 'application/json',
      headers: { 'X-DataLife-Token': token },
      payload: JSON.stringify({ origen: 'google_forms', fila: e && e.range ? e.range.getRow() : null }),
      muteHttpExceptions: true
    });
  } catch (err) {
    console.error('No se pudo avisar a DataLife: ' + err);
  }
}
