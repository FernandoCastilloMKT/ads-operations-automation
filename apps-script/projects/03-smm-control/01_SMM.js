const SMM_SHEET = 'Hoja de Control';
const SMM_BUTTON_CELL = 'Q2';
const SMM_LABEL_CELL = 'R2';
const SMM_STATUS_CELL = 'R3';
const SMM_MARKER = 'SMM_REFRESH_ALL_CHECKBOX';
const SMM_PENDING_PREFIX = 'SMM_PENDING_';
const SMM_LAST_DISPATCH = 'SMM_LAST_DISPATCH';
const SMM_COOLDOWN_MS = 120000;
const SMM_TIMEOUT_MINUTES = 45;

function smmSpreadsheet_() {
  return SpreadsheetApp.openById(RuntimeConfig.required('SMM_SPREADSHEET_ID'));
}

function SMM_04_botonManualEditado(event) {
  if (!event || !event.range || String(event.value).toUpperCase() !== 'TRUE') return;
  if (event.range.getNote() !== SMM_MARKER) return;
  event.range.setValue(false);
  smmLanzarActualizacion_();
}

function smmLanzarActualizacion_() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  const spreadsheet = smmSpreadsheet_();
  try {
    const properties = PropertiesService.getScriptProperties();
    const pending = properties.getKeys().some((key) => key.startsWith(SMM_PENDING_PREFIX));
    if (pending) {
      smmEstado_('Actualizacion en curso', '#fbbc04');
      spreadsheet.toast('Ya hay una actualizacion SMM en curso.', 'Actualizar fichas SMM', 8);
      return;
    }
    const now = Date.now();
    const last = Number(properties.getProperty(SMM_LAST_DISPATCH) || 0);
    if (last && now - last < SMM_COOLDOWN_MS) {
      spreadsheet.toast('Espera dos minutos antes de repetir la solicitud.', 'Actualizar fichas SMM', 8);
      return;
    }
    const github = RuntimeConfig.github('SMM_GITHUB_WORKFLOW', 'actualizar-smm.yml');
    const requestId = Utilities.getUuid();
    smmEstado_('Actualizacion en curso', '#fbbc04');
    const response = UrlFetchApp.fetch(
      `https://api.github.com/repos/${github.owner}/${github.repo}/actions/workflows/${github.workflow}/dispatches`,
      {
        method: 'post',
        contentType: 'application/json',
        headers: {
          Authorization: `Bearer ${RuntimeConfig.required('GITHUB_TOKEN')}`,
          Accept: 'application/vnd.github+json',
          'X-GitHub-Api-Version': '2022-11-28',
        },
        payload: JSON.stringify({ref: github.branch, inputs: {request_id: requestId}}),
        muteHttpExceptions: true,
      }
    );
    if (![200, 204].includes(response.getResponseCode())) {
      throw new Error(`GitHub devolvio ${response.getResponseCode()}.`);
    }
    properties.setProperty(SMM_LAST_DISPATCH, String(now));
    properties.setProperty(`${SMM_PENDING_PREFIX}${requestId}`, JSON.stringify({
      requestId: requestId,
      requestedAt: now,
      spreadsheetId: spreadsheet.getId(),
    }));
    smmAsegurarComprobador_();
    spreadsheet.toast('Actualizacion enviada a GitHub.', 'Actualizar fichas SMM', 8);
  } catch (error) {
    smmEstado_('Error al enviar', '#ff0000');
    spreadsheet.toast(error.message, 'Error al actualizar SMM', 10);
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function SMM_03_comprobarFinalizaciones() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) return;
  try {
    const properties = PropertiesService.getScriptProperties();
    const keys = properties.getKeys().filter((key) => key.startsWith(SMM_PENDING_PREFIX));
    if (!keys.length) return smmEliminarComprobadores_();
    const github = RuntimeConfig.github('SMM_GITHUB_WORKFLOW', 'actualizar-smm.yml');
    const response = UrlFetchApp.fetch(
      `https://api.github.com/repos/${github.owner}/${github.repo}/actions/workflows/${github.workflow}/runs?event=workflow_dispatch&per_page=30`,
      {headers: {Authorization: `Bearer ${RuntimeConfig.required('GITHUB_TOKEN')}`, Accept: 'application/vnd.github+json'}}
    );
    const runs = JSON.parse(response.getContentText()).workflow_runs || [];
    keys.forEach((key) => {
      const pending = JSON.parse(properties.getProperty(key));
      const run = runs.find((item) => String(item.display_title || '').includes(pending.requestId));
      const age = (Date.now() - pending.requestedAt) / 60000;
      if (run && run.status === 'completed') {
        smmFinalizar_(properties, key, run.conclusion, run.html_url || '');
      } else if (age > SMM_TIMEOUT_MINUTES) {
        smmFinalizar_(properties, key, 'failure', run ? run.html_url : '');
      }
    });
    if (!properties.getKeys().some((key) => key.startsWith(SMM_PENDING_PREFIX))) {
      smmEliminarComprobadores_();
    }
  } finally {
    lock.releaseLock();
  }
}

function smmFinalizar_(properties, key, conclusion, runUrl) {
  const success = conclusion === 'success';
  const text = success
    ? `Actualizado ${Utilities.formatDate(new Date(), 'Europe/Madrid', 'dd/MM/yyyy HH:mm')}`
    : 'Error en la actualizacion';
  smmEstado_(text, success ? '#ffffff' : '#ff0000', runUrl);
  properties.deleteProperty(key);
}

function smmEstado_(text, color, runUrl) {
  const sheet = smmSpreadsheet_().getSheetByName(SMM_SHEET);
  const cell = sheet.getRange(SMM_STATUS_CELL);
  cell.setValue(text).setBackground(color).setFontColor('#000000');
  cell.setNote(runUrl ? `Ejecucion: ${runUrl}` : '');
}

function smmAsegurarComprobador_() {
  const exists = ScriptApp.getProjectTriggers().some(
    (trigger) => trigger.getHandlerFunction() === 'SMM_03_comprobarFinalizaciones'
  );
  if (!exists) ScriptApp.newTrigger('SMM_03_comprobarFinalizaciones').timeBased().everyMinutes(1).create();
}

function smmEliminarComprobadores_() {
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'SMM_03_comprobarFinalizaciones') ScriptApp.deleteTrigger(trigger);
  });
}

function SMM_92_instalarControl() {
  const sheet = smmSpreadsheet_().getSheetByName(SMM_SHEET);
  if (!sheet) throw new Error(`No existe ${SMM_SHEET}.`);
  sheet.getRange(SMM_BUTTON_CELL).insertCheckboxes().setValue(false)
    .setNote(SMM_MARKER).setBackground('#1a73e8').setHorizontalAlignment('center');
  sheet.getRange(SMM_LABEL_CELL).setValue('Actualizar todas las fichas')
    .setBackground('#1a73e8').setFontColor('#ffffff').setFontWeight('bold');
  sheet.getRange(SMM_STATUS_CELL).setValue('Listo para actualizar')
    .setBackground('#ffffff').setFontColor('#5f6368').setFontSize(8);
  return 'Control SMM instalado en Q2:R3.';
}

function SMM_98_instalarActivadores() {
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (trigger.getHandlerFunction() === 'SMM_04_botonManualEditado') ScriptApp.deleteTrigger(trigger);
  });
  ScriptApp.newTrigger('SMM_04_botonManualEditado')
    .forSpreadsheet(RuntimeConfig.required('SMM_SPREADSHEET_ID'))
    .onEdit()
    .create();
  return 'Activador manual SMM instalado.';
}

function SMM_91_auditarControl() {
  const sheet = smmSpreadsheet_().getSheetByName(SMM_SHEET);
  const cell = sheet.getRange(SMM_BUTTON_CELL);
  const validation = cell.getDataValidation();
  if (cell.getNote() !== SMM_MARKER || !validation ||
      validation.getCriteriaType() !== SpreadsheetApp.DataValidationCriteria.CHECKBOX) {
    throw new Error('La casilla SMM no esta instalada correctamente.');
  }
  const triggers = ScriptApp.getProjectTriggers().filter(
    (trigger) => trigger.getHandlerFunction() === 'SMM_04_botonManualEditado'
  );
  if (triggers.length !== 1) throw new Error('Se esperaba un unico activador onEdit SMM.');
  return 'Control SMM correcto.';
}
