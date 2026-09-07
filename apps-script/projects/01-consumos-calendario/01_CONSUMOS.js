const CONSUMOS_TIME_ZONE = 'Europe/Madrid';
const CONSUMOS_ORIGIN_MANUAL = 'manual';
const CONSUMOS_ORIGIN_SCHEDULED = 'apps-script';
const CONSUMOS_MANUAL_BUTTON_HANDLER = 'CONSUMOS_03_botonManualEditado';
const CONSUMOS_MANUAL_BUTTON_MARKER = 'CONTROL_CONSUMOS_MANUAL';
const CONSUMOS_MANUAL_STATUS_SEPARATOR = '|STATUS=';
const CONSUMOS_MANUAL_BUTTON_COOLDOWN_SECONDS = 120;
const CONSUMOS_MANUAL_BUTTON_PROPERTY_PREFIX = 'CONSUMOS_LAST_MANUAL_';
const CONSUMOS_PENDING_PROPERTY_PREFIX = 'CONSUMOS_PENDING_';
const CONSUMOS_COMPLETION_HANDLER = 'CONSUMOS_04_comprobarFinalizaciones';
const CONSUMOS_COMPLETION_TIMEOUT_MINUTES = 90;
const CONSUMOS_SCHEDULES = [
  {
    handler: 'CONSUMOS_01_ejecutarCompletoProgramado',
    hour: 7,
    minute: 40,
  },
  {
    handler: 'CONSUMOS_02_ejecutarRapidoProgramado',
    hour: 15,
    minute: 10,
  },
];
const CONSUMOS_LEGACY_TRIGGER_HANDLERS = [
  'ejecutarCompleto',
  'ejecutarRapido',
];

function CONSUMOS_01_ejecutarCompletoProgramado() {
  consumosLanzarActualizacion_('completo', CONSUMOS_ORIGIN_SCHEDULED);
}

function CONSUMOS_02_ejecutarRapidoProgramado() {
  consumosLanzarActualizacion_('rapido', CONSUMOS_ORIGIN_SCHEDULED);
}

function CONSUMOS_90_probarCompletoAhora() {
  consumosLanzarActualizacion_('completo', CONSUMOS_ORIGIN_MANUAL);
}

function CONSUMOS_91_probarRapidoAhora() {
  consumosLanzarActualizacion_('rapido', CONSUMOS_ORIGIN_MANUAL);
}

function CONSUMOS_94_probarControlManual(cliente) {
  const requestedClient = String(cliente || '').trim();
  if (!requestedClient) {
    throw new Error('Indica el nombre exacto del cliente configurado.');
  }
  const mapping = consumosSpreadsheetClientMap_();
  const matches = Object.keys(mapping).filter(
    (spreadsheetId) => mapping[spreadsheetId] === requestedClient
  );
  if (matches.length !== 1) {
    throw new Error(
      `Se esperaban una coincidencia para ${requestedClient}; ` +
        `se encontraron ${matches.length}.`
    );
  }
  const spreadsheet = SpreadsheetApp.openById(matches[0]);
  const controls = consumosLocalizarControlesSpreadsheet_(spreadsheet);
  if (controls.length !== 1) {
    throw new Error(
      `${spreadsheet.getName()}: ${controls.length} controles manuales.`
    );
  }
  const checkbox = controls[0].checkbox;
  checkbox.setValue(true);
  CONSUMOS_03_botonManualEditado({
    source: spreadsheet,
    range: checkbox,
    value: 'TRUE',
  });
}

function CONSUMOS_03_botonManualEditado(event) {
  if (
    !event ||
    !event.range ||
    String(event.value || '').toUpperCase() !== 'TRUE' ||
    !consumosEsMarcadorControl_(event.range.getNote())
  ) {
    return;
  }

  const spreadsheet = event.source;
  const checkbox = event.range;
  const sheetName = checkbox.getSheet().getName();
  checkbox.setValue(false);

  const mapping = consumosSpreadsheetClientMap_();
  const cliente = mapping[spreadsheet.getId()];
  if (!cliente) {
    spreadsheet.toast(
      'Este Google Sheet no esta configurado para la actualizacion manual.',
      'Actualizar consumos',
      7
    );
    return;
  }

  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    spreadsheet.toast(
      'Ya se esta enviando otra solicitud. Espera unos segundos.',
      'Actualizar consumos',
      7
    );
    return;
  }

  try {
    const properties = PropertiesService.getScriptProperties();
    const key =
      `${CONSUMOS_MANUAL_BUTTON_PROPERTY_PREFIX}` +
      Utilities.base64EncodeWebSafe(cliente).replace(/=+$/g, '');
    const now = Date.now();
    const lastDispatch = Number(properties.getProperty(key) || 0);
    const cooldownMs = CONSUMOS_MANUAL_BUTTON_COOLDOWN_SECONDS * 1000;

    if (consumosTienePendienteCliente_(properties, cliente)) {
      consumosActualizarEstadoControl_(
        spreadsheet,
        sheetName,
        'Actualizacion en curso',
        '#fbbc04'
      );
      spreadsheet.toast(
        'Ya hay una actualizacion de este Google Sheet en curso.',
        'Actualizar consumos',
        8
      );
      return;
    }

    if (lastDispatch && now - lastDispatch < cooldownMs) {
      const seconds = Math.ceil((cooldownMs - (now - lastDispatch)) / 1000);
      spreadsheet.toast(
        `La actualizacion ya fue solicitada. Podras volver a enviarla en ` +
          `${seconds} segundos. Este contador no es el tiempo de ejecucion.`,
        'Actualizar consumos',
        8
      );
      return;
    }

    const requestId = Utilities.getUuid();
    consumosActualizarEstadoControl_(
      spreadsheet,
      sheetName,
      'Actualizacion en curso',
      '#fbbc04'
    );
    consumosLanzarActualizacion_(
      'completo',
      CONSUMOS_ORIGIN_MANUAL,
      cliente,
      requestId
    );
    consumosRegistrarPendiente_(
      properties,
      requestId,
      spreadsheet.getId(),
      sheetName,
      cliente,
      now
    );
    properties.setProperty(key, String(now));
    spreadsheet.toast(
      'Actualizacion completa enviada. GitHub trabajara en segundo plano; ' +
        'la hoja cambiara cuando termine.',
      'Actualizar consumos',
      8
    );
  } catch (error) {
    consumosActualizarEstadoControl_(
      spreadsheet,
      sheetName,
      'Error al enviar',
      '#ff0000'
    );
    spreadsheet.toast(
      `No se pudo enviar la actualizacion: ${error.message}`,
      'Actualizar consumos',
      10
    );
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function CONSUMOS_04_comprobarFinalizaciones() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    return;
  }

  try {
    const properties = PropertiesService.getScriptProperties();
    const pendingKeys = properties
      .getKeys()
      .filter((key) => key.startsWith(CONSUMOS_PENDING_PROPERTY_PREFIX));
    if (!pendingKeys.length) {
      consumosEliminarActivadoresFinalizacion_();
      return;
    }

    const token = RuntimeConfig.required('GITHUB_TOKEN');
    const github = RuntimeConfig.github(
      'CONSUMOS_GITHUB_WORKFLOW',
      'actualizar-consumos.yml'
    );
    const url =
      `https://api.github.com/repos/${github.owner}/${github.repo}` +
      `/actions/workflows/${github.workflow}/runs` +
      '?event=workflow_dispatch&per_page=100';
    const response = UrlFetchApp.fetch(url, {
      method: 'get',
      headers: {
        Authorization: `Bearer ${token}`,
        Accept: 'application/vnd.github+json',
        'X-GitHub-Api-Version': '2022-11-28',
      },
      muteHttpExceptions: true,
    });
    if (response.getResponseCode() !== 200) {
      throw new Error(
        `GitHub devolvio ${response.getResponseCode()} al consultar runs.`
      );
    }

    const runs = JSON.parse(response.getContentText()).workflow_runs || [];
    const now = Date.now();

    pendingKeys.forEach((key) => {
      const pending = JSON.parse(properties.getProperty(key));
      const run = runs.find((item) =>
        String(item.display_title || '').includes(pending.requestId)
      );
      const ageMinutes = (now - Number(pending.requestedAt)) / 60000;

      if (!run && ageMinutes <= CONSUMOS_COMPLETION_TIMEOUT_MINUTES) {
        return;
      }

      if (run && run.status !== 'completed') {
        return;
      }

      if (!run) {
        consumosFinalizarPendiente_(properties, key, pending, {
          conclusion: 'unconfirmed',
          runUrl: '',
          finishedAt: new Date(),
        });
      } else {
        consumosFinalizarPendiente_(properties, key, pending, {
          conclusion: run.conclusion || 'unknown',
          runUrl: run.html_url || '',
          finishedAt: new Date(run.updated_at),
        });
      }
    });

    const remaining = properties
      .getKeys()
      .some((key) => key.startsWith(CONSUMOS_PENDING_PROPERTY_PREFIX));
    if (!remaining) {
      consumosEliminarActivadoresFinalizacion_();
    }
  } finally {
    lock.releaseLock();
  }
}

function consumosFinalizarPendiente_(
  properties,
  pendingKey,
  pending,
  result
) {
  const spreadsheet = SpreadsheetApp.openById(pending.spreadsheetId);
  const conclusion = String(result.conclusion || 'unknown').toLowerCase();
  const runUrl = String(result.runUrl || '');
  const parsedFinishedAt = new Date(result.finishedAt || Date.now());
  const finishedAt = Number.isNaN(parsedFinishedAt.getTime())
    ? new Date()
    : parsedFinishedAt;
  let toastMessage = '';
  let toastTitle = 'Actualizar consumos';

  if (conclusion === 'success') {
    const finishedLabel = Utilities.formatDate(
      finishedAt,
      CONSUMOS_TIME_ZONE,
      'dd/MM/yyyy HH:mm'
    );
    consumosActualizarEstadoControl_(
      spreadsheet,
      pending.sheetName,
      `Actualizado ${finishedLabel}`,
      '#34a853',
      runUrl
    );
    toastMessage =
      'Actualizacion finalizada correctamente. Todos los datos del ' +
      'Google Sheet ya estan actualizados.';
    toastTitle = 'Consumos actualizados';
  } else if (conclusion === 'unconfirmed') {
    consumosActualizarEstadoControl_(
      spreadsheet,
      pending.sheetName,
      'Sin confirmacion de GitHub',
      '#ff0000'
    );
    toastMessage =
      'No se pudo confirmar la finalizacion. Revisa GitHub Actions.';
  } else {
    consumosActualizarEstadoControl_(
      spreadsheet,
      pending.sheetName,
      `Error en GitHub: ${conclusion}`,
      '#ff0000',
      runUrl
    );
    toastMessage =
      'La actualizacion termino con error. Revisa GitHub Actions.';
    toastTitle = 'Error al actualizar consumos';
  }

  // El estado persistente es la fuente de verdad. La notificacion visual no
  // esta disponible desde todos los contextos de Apps Script (por ejemplo,
  // Execution API o algunos activadores), por lo que el bloqueo se elimina
  // antes de intentar mostrarla.
  properties.deleteProperty(pendingKey);
  consumosToastSeguro_(spreadsheet, toastMessage, toastTitle, 10);
}

function consumosToastSeguro_(spreadsheet, message, title, seconds) {
  try {
    spreadsheet.toast(message, title, seconds);
  } catch (error) {
    console.warn(
      `No se pudo mostrar la notificacion de consumos: ${error.message}`
    );
  }
}

function CONSUMOS_92_instalarControlesManuales() {
  const mapping = consumosSpreadsheetClientMap_();
  const spreadsheetIds = Object.keys(mapping);
  if (!spreadsheetIds.length) {
    throw new Error('No hay Google Sheets configurados para consumos.');
  }

  const currentYear = Utilities.formatDate(
    new Date(),
    CONSUMOS_TIME_ZONE,
    'yyyy'
  );
  const sheetName = `Consumo ${currentYear} G. Ads`;

  spreadsheetIds.forEach((spreadsheetId) => {
    const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    const controls = consumosLocalizarControlesSpreadsheet_(spreadsheet);
    const customControls = controls
      .filter((control) => !/^Consumo \d{4} G\. Ads$/i.test(
        control.checkbox.getSheet().getName()
      ));
    if (customControls.length > 1) {
      throw new Error(
        `${spreadsheet.getName()}: hay varios controles personalizados.`
      );
    }
    if (customControls.length === 1) {
      controls
        .filter((control) => /^Consumo \d{4} G\. Ads$/i.test(
          control.checkbox.getSheet().getName()
        ))
        .forEach((control) => consumosRetirarControlHistorico_(control));
      return;
    }
    const sheet = spreadsheet.getSheetByName(sheetName);
    if (!sheet) {
      throw new Error(
        `No se encontro ${sheetName} en ${spreadsheet.getName()}.`
      );
    }
    consumosInstalarControlEnHoja_(sheet, currentYear);
  });

  ScriptApp.getProjectTriggers()
    .filter(
      (trigger) =>
        trigger.getHandlerFunction() === CONSUMOS_MANUAL_BUTTON_HANDLER
    )
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));

  spreadsheetIds.forEach((spreadsheetId) => {
    ScriptApp.newTrigger(CONSUMOS_MANUAL_BUTTON_HANDLER)
      .forSpreadsheet(spreadsheetId)
      .onEdit()
      .create();
  });

  Logger.log(
    `CONSUMOS: ${spreadsheetIds.length} controles manuales y activadores ` +
      `instalados en ${sheetName}.`
  );
}

function consumosRetirarControlHistorico_(control) {
  const checkbox = control.checkbox;
  const sheet = checkbox.getSheet();
  if (!/^Consumo \d{4} G\. Ads$/i.test(sheet.getName())) {
    throw new Error(
      `No se puede retirar un control fuera de una historica: ` +
        `${sheet.getName()}.`
    );
  }

  const row = checkbox.getRow();
  const column = checkbox.getColumn();
  const legacyRange = sheet.getRange(row, column, 2, 2);
  legacyRange.clear();
  legacyRange.clearNote();
  legacyRange.clearDataValidations();
  Logger.log(
    `CONSUMOS: control historico retirado de ${sheet.getName()} ` +
      `(${checkbox.getA1Notation()}).`
  );
}

function CONSUMOS_93_auditarControlesManuales() {
  const mapping = consumosSpreadsheetClientMap_();
  const spreadsheetIds = Object.keys(mapping);
  const currentYear = Utilities.formatDate(
    new Date(),
    CONSUMOS_TIME_ZONE,
    'yyyy'
  );
  const sheetName = `Consumo ${currentYear} G. Ads`;
  const errors = [];

  spreadsheetIds.forEach((spreadsheetId) => {
    const spreadsheet = SpreadsheetApp.openById(spreadsheetId);
    const controls = consumosLocalizarControlesSpreadsheet_(spreadsheet);
    if (controls.length !== 1) {
      errors.push(
        `${spreadsheet.getName()}: ${controls.length} controles manuales`
      );
    }

    const triggerCount = ScriptApp.getProjectTriggers().filter(
      (trigger) =>
        trigger.getHandlerFunction() === CONSUMOS_MANUAL_BUTTON_HANDLER &&
        trigger.getTriggerSourceId() === spreadsheetId
    ).length;
    if (triggerCount !== 1) {
      errors.push(
        `${spreadsheet.getName()}: ${triggerCount} activadores manuales`
      );
    }
  });

  if (errors.length) {
    throw new Error(
      `Auditoria de controles manuales incorrecta: ${errors.join('; ')}`
    );
  }
  Logger.log(
    `CONSUMOS: ${spreadsheetIds.length} controles manuales correctos.`
  );
}

function CONSUMOS_98_instalarActivadores() {
  const handlers = new Set([
    ...CONSUMOS_SCHEDULES.map((schedule) => schedule.handler),
    ...CONSUMOS_LEGACY_TRIGGER_HANDLERS,
  ]);

  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (handlers.has(trigger.getHandlerFunction())) {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  CONSUMOS_SCHEDULES.forEach((schedule) => {
    ScriptApp.newTrigger(schedule.handler)
      .timeBased()
      .atHour(schedule.hour)
      .nearMinute(schedule.minute)
      .everyDays(1)
      .inTimezone(CONSUMOS_TIME_ZONE)
      .create();
  });

  Logger.log(
    'Activadores de consumos instalados: 07:40 completo y 15:10 rapido.'
  );
}

function CONSUMOS_99_auditarActivadores() {
  const triggers = ScriptApp.getProjectTriggers();
  const errors = [];

  CONSUMOS_SCHEDULES.forEach((schedule) => {
    const count = triggers.filter(
      (trigger) =>
        trigger.getHandlerFunction() === schedule.handler &&
        trigger.getEventType() === ScriptApp.EventType.CLOCK
    ).length;
    if (count !== 1) {
      errors.push(`${schedule.handler}: ${count}`);
    }
  });

  const legacy = triggers
    .map((trigger) => trigger.getHandlerFunction())
    .filter((handler) => CONSUMOS_LEGACY_TRIGGER_HANDLERS.includes(handler));
  if (legacy.length) {
    errors.push(`activadores antiguos: ${legacy.join(', ')}`);
  }

  if (errors.length) {
    throw new Error(`Auditoria de consumos incorrecta: ${errors.join('; ')}`);
  }

  Logger.log(
    'CONSUMOS: auditoria correcta; hay exactamente dos activadores diarios.'
  );
}

function consumosLanzarActualizacion_(
  modo,
  origen = CONSUMOS_ORIGIN_MANUAL,
  cliente = '',
  requestId = ''
) {
  if (!['completo', 'rapido'].includes(modo)) {
    throw new Error(`Modo de consumos no valido: ${modo}`);
  }
  if (![CONSUMOS_ORIGIN_MANUAL, CONSUMOS_ORIGIN_SCHEDULED].includes(origen)) {
    throw new Error(`Origen de consumos no valido: ${origen}`);
  }

  const token = RuntimeConfig.required('GITHUB_TOKEN');
  const github = RuntimeConfig.github(
    'CONSUMOS_GITHUB_WORKFLOW',
    'actualizar-consumos.yml'
  );

  const url =
    `https://api.github.com/repos/${github.owner}/` +
    `${github.repo}/actions/workflows/` +
    `${github.workflow}/dispatches`;

  const response = UrlFetchApp.fetch(url, {
    method: 'post',
    contentType: 'application/json',
    headers: {
      Authorization: `Bearer ${token}`,
      Accept: 'application/vnd.github+json',
      'X-GitHub-Api-Version': '2022-11-28',
    },
    payload: JSON.stringify({
      ref: github.branch,
      inputs: {
        modo: modo,
        origen: origen,
        cliente: cliente,
        request_id: requestId,
      },
    }),
    muteHttpExceptions: true,
  });

  const code = response.getResponseCode();
  if (code !== 204) {
    throw new Error(`GitHub devolvio ${code}: ${response.getContentText()}`);
  }

  Logger.log(
    `Workflow de consumos lanzado correctamente en modo ${modo}; ` +
      `origen ${origen}; cliente ${cliente || 'TODOS'}.`
  );
}

function consumosRegistrarPendiente_(
  properties,
  requestId,
  spreadsheetId,
  sheetName,
  cliente,
  requestedAt
) {
  properties.setProperty(
    `${CONSUMOS_PENDING_PROPERTY_PREFIX}${requestId}`,
    JSON.stringify({
      requestId: requestId,
      spreadsheetId: spreadsheetId,
      sheetName: sheetName,
      cliente: cliente,
      requestedAt: requestedAt,
    })
  );

  const triggerExists = ScriptApp.getProjectTriggers().some(
    (trigger) =>
      trigger.getHandlerFunction() === CONSUMOS_COMPLETION_HANDLER
  );
  if (!triggerExists) {
    ScriptApp.newTrigger(CONSUMOS_COMPLETION_HANDLER)
      .timeBased()
      .everyMinutes(1)
      .create();
  }
}

function consumosTienePendienteCliente_(properties, cliente) {
  return properties
    .getKeys()
    .filter((key) => key.startsWith(CONSUMOS_PENDING_PROPERTY_PREFIX))
    .some((key) => {
      try {
        return JSON.parse(properties.getProperty(key)).cliente === cliente;
      } catch (error) {
        properties.deleteProperty(key);
        return false;
      }
    });
}

function consumosEliminarActivadoresFinalizacion_() {
  ScriptApp.getProjectTriggers()
    .filter(
      (trigger) =>
        trigger.getHandlerFunction() === CONSUMOS_COMPLETION_HANDLER
    )
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
}

function consumosSpreadsheetClientMap_() {
  const mapping = RuntimeConfig.json(
    'CONSUMOS_SPREADSHEET_CLIENT_MAP_JSON'
  );
  if (
    !mapping ||
    Array.isArray(mapping) ||
    Object.keys(mapping).length === 0
  ) {
    throw new Error(
      'CONSUMOS_SPREADSHEET_CLIENT_MAP_JSON debe mapear Sheets a clientes.'
    );
  }
  return mapping;
}

function consumosInstalarControlEnHoja_(sheet, year) {
  const values = sheet.getRange(1, 1, 3, sheet.getLastColumn()).getValues();
  let decemberColumn = 0;

  for (let column = 1; column <= values[0].length; column += 1) {
    const candidates = values.map((row) =>
      String(row[column - 1] || '')
        .trim()
        .toLowerCase()
    );
    if (
      candidates.includes(`${year}|12`) ||
      candidates.includes(`${year}-12`) ||
      candidates.includes('diciembre')
    ) {
      decemberColumn = column;
      break;
    }
  }

  if (!decemberColumn) {
    throw new Error(
      `No se encontro la columna de diciembre en ${sheet.getName()}.`
    );
  }

  const requiredColumns = decemberColumn + 2;
  if (sheet.getMaxColumns() < requiredColumns) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      requiredColumns - sheet.getMaxColumns()
    );
  }

  const checkbox = sheet.getRange(2, decemberColumn + 1);
  const label = sheet.getRange(2, decemberColumn + 2);
  checkbox
    .insertCheckboxes()
    .setValue(false)
    .setNote(CONSUMOS_MANUAL_BUTTON_MARKER)
    .setBackground('#1a73e8')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setBorder(true, true, true, false, false, false, '#1557b0',
      SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  label
    .setValue('Actualizar consumos')
    .setNote(
      'Marca la casilla azul de la izquierda para ejecutar este Google Sheet.'
    )
    .setBackground('#1a73e8')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setBorder(true, false, true, true, false, false, '#1557b0',
      SpreadsheetApp.BorderStyle.SOLID_MEDIUM);
  const status = sheet.getRange(3, decemberColumn + 2);
  if (!String(status.getValue() || '').trim()) {
    status
      .setValue('Listo para actualizar')
      .setFontColor('#5f6368')
      .setFontSize(8)
      .setHorizontalAlignment('center');
  }
  sheet.setColumnWidth(decemberColumn + 1, 34);
  sheet.setColumnWidth(decemberColumn + 2, 145);
  sheet.setRowHeight(2, 34);
}

function consumosEsMarcadorControl_(note) {
  const value = String(note || '');
  return value === CONSUMOS_MANUAL_BUTTON_MARKER ||
    value.startsWith(
      `${CONSUMOS_MANUAL_BUTTON_MARKER}${CONSUMOS_MANUAL_STATUS_SEPARATOR}`
    );
}

function consumosCeldaEstadoControl_(checkbox) {
  const note = String(checkbox.getNote() || '');
  const marker =
    `${CONSUMOS_MANUAL_BUTTON_MARKER}${CONSUMOS_MANUAL_STATUS_SEPARATOR}`;
  if (note.startsWith(marker)) {
    const a1 = note.slice(marker.length).trim();
    if (a1) {
      return checkbox.getSheet().getRange(a1);
    }
  }
  return checkbox.getSheet().getRange(
    checkbox.getRow() + 1,
    checkbox.getColumn() + 1
  );
}

function consumosLocalizarControl_(sheet, year) {
  const lastColumn = sheet.getLastColumn();
  const lastRow = Math.min(Math.max(sheet.getLastRow(), 1), 20);
  if (lastColumn < 1) {
    return null;
  }
  const notes = sheet.getRange(1, 1, lastRow, lastColumn).getNotes();
  for (let row = 0; row < notes.length; row += 1) {
    for (let column = 0; column < notes[row].length; column += 1) {
      if (!consumosEsMarcadorControl_(notes[row][column])) {
        continue;
      }
      const checkbox = sheet.getRange(row + 1, column + 1);
      const validation = checkbox.getDataValidation();
      if (
        validation &&
        validation.getCriteriaType() ===
          SpreadsheetApp.DataValidationCriteria.CHECKBOX
      ) {
        return checkbox;
      }
    }
  }
  return null;
}

function consumosLocalizarControlesSpreadsheet_(spreadsheet) {
  return spreadsheet.getSheets()
    .filter((sheet) =>
      sheet.getName() === 'Resumen' ||
      /^Consumo \d{4} G\. Ads$/i.test(sheet.getName())
    )
    .map((sheet) => {
      const checkbox = consumosLocalizarControl_(sheet, '');
      return checkbox ? {
        checkbox: checkbox,
        status: consumosCeldaEstadoControl_(checkbox),
      } : null;
    })
    .filter((control) => control !== null);
}

function consumosActualizarEstadoControl_(
  spreadsheet,
  sheetName,
  message,
  color,
  runUrl = ''
) {
  const sheet = spreadsheet.getSheetByName(sheetName);
  if (!sheet) {
    return;
  }
  const yearMatch = sheetName.match(/\d{4}/);
  const checkbox = consumosLocalizarControl_(
    sheet,
    yearMatch ? yearMatch[0] : ''
  );
  if (!checkbox) {
    return;
  }
  const status = consumosCeldaEstadoControl_(checkbox);
  status
    .setValue(message)
    .setNote(runUrl ? `Ejecucion: ${runUrl}` : '')
    .setFontColor(color)
    .setFontWeight('bold')
    .setFontSize(8)
    .setHorizontalAlignment('center');
}
