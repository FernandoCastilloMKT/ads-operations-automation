const SEM_ALL_CLIENTS_VALUE = 'TODAS';
const SEM_BUTTON_MARKER = 'FICHAS_SEM_REFRESH_BUTTON';
const SEM_BUTTON_COLUMN = 18; // Posicion predeterminada: columna R.
const SEM_BUTTON_ROW = 2;
const SEM_BUTTON_SIZE = 36;
const SEM_CONTROL_MARKER = 'FICHAS_SEM_REFRESH_CHECKBOX';
const SEM_MANUAL_EDIT_HANDLER = 'FICHAS_04_botonManualEditado';
const SEM_TIME_ZONE = 'Europe/Madrid';
const SEM_DISPATCH_LOCK_WAIT_MS = 5000;
const SEM_DISPATCH_COOLDOWN_SECONDS = 120;
const SEM_DISPATCH_PROPERTY_PREFIX = 'SEM_LAST_DISPATCH_';
const SEM_PENDING_PROPERTY_PREFIX = 'SEM_PENDING_';
const SEM_COMPLETION_HANDLER = 'FICHAS_03_comprobarFinalizaciones';
const SEM_COMPLETION_TIMEOUT_MINUTES = 90;
const SEM_SINGLE_BUTTON_HANDLER = 'FICHAS_01_actualizarEstaFicha';
const SEM_ALL_BUTTON_HANDLER = 'FICHAS_02_actualizarTodas';
const SEM_SCHEDULED_HANDLER = 'FICHAS_10_actualizarTodasProgramado';
const SEM_STATUS_COLUMN = 19; // Posicion predeterminada: columna S.
const SEM_STATUS_ROW = 3;
const SEM_SCHEDULES = [
  { handler: SEM_SCHEDULED_HANDLER, hour: 7, minute: 35 },
  { handler: SEM_SCHEDULED_HANDLER, hour: 12, minute: 50 },
];
const SEM_LEGACY_TRIGGER_HANDLERS = [
  'ejecutarTodasSemManana',
  'ejecutarTodasSemMediodia',
];
const SEM_LEGACY_BUTTON_HANDLERS = [
  'actualizarFichaActual',
  'actualizarTodasLasFichas',
];

function fichasLegacyButtonHandlers_() {
  return [
    ...SEM_LEGACY_BUTTON_HANDLERS,
    ...RuntimeConfig.optionalJson(
      'SEM_LEGACY_BUTTON_HANDLERS_JSON',
      []
    ),
  ];
}

function fichasWorksheetMap_() {
  const mapping = RuntimeConfig.json('SEM_WORKSHEET_MAP_JSON');
  if (
    !mapping ||
    Array.isArray(mapping) ||
    Object.keys(mapping).length === 0
  ) {
    throw new Error(
      'SEM_WORKSHEET_MAP_JSON debe mapear pestanas a claves client_NNN.'
    );
  }
  return mapping;
}

function fichasBuscarColumnaControl_(sheet) {
  const maxColumns = sheet.getMaxColumns();
  const controlRow = sheet.getRange(
    SEM_BUTTON_ROW,
    1,
    1,
    maxColumns
  );
  const notes = controlRow.getNotes()[0];
  const validations = controlRow.getDataValidations()[0];

  for (let index = 0; index < maxColumns; index += 1) {
    const validation = validations[index];
    if (
      notes[index] === SEM_CONTROL_MARKER &&
      validation &&
      validation.getCriteriaType() ===
        SpreadsheetApp.DataValidationCriteria.CHECKBOX
    ) {
      return index + 1;
    }
  }
  return null;
}

function fichasEsEstadoGestionado_(value) {
  return /^(Listo|Actualizaci[oó]n|Actualizado|Error|Sin confirmaci[oó]n)/i
    .test(String(value || '').trim());
}

const SEM_SINGLE_BUTTON_PNG =
  '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';
const SEM_ALL_BUTTON_PNG =
  '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';

function FICHAS_01_actualizarEstaFicha() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const sheetName = spreadsheet.getActiveSheet().getName();
  const clientKey = fichasWorksheetMap_()[sheetName];

  if (!clientKey) {
    SpreadsheetApp.getUi().alert(
      `La pestana ${sheetName} no esta habilitada para FICHAS_SEM_CONTROL.`
    );
    return;
  }

  fichasLanzarActualizacion_(clientKey, true, sheetName);
}

function FICHAS_02_actualizarTodas() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const activeSheetName = spreadsheet.getActiveSheet().getName();
  const globalSheetName = RuntimeConfig.required('SEM_GLOBAL_SHEET');

  if (activeSheetName !== globalSheetName) {
    SpreadsheetApp.getUi().alert(
      `La actualizacion general solo puede lanzarse desde ${globalSheetName}.`
    );
    return;
  }

  fichasLanzarActualizacion_(
    SEM_ALL_CLIENTS_VALUE,
    true,
    'todas las fichas',
    activeSheetName
  );
}

function FICHAS_04_botonManualEditado(event) {
  if (
    !event ||
    !event.range ||
    String(event.value || '').toUpperCase() !== 'TRUE' ||
    event.range.getNote() !== SEM_CONTROL_MARKER
  ) {
    return;
  }

  const spreadsheet = event.source;
  const checkbox = event.range;
  const sheetName = checkbox.getSheet().getName();
  const globalSheetName = RuntimeConfig.required('SEM_GLOBAL_SHEET');
  checkbox.setValue(false);

  if (sheetName === globalSheetName) {
    fichasLanzarActualizacion_(
      SEM_ALL_CLIENTS_VALUE,
      true,
      'todas las fichas',
      sheetName
    );
    return;
  }

  const clientKey = fichasWorksheetMap_()[sheetName];
  if (!clientKey) {
    spreadsheet.toast(
      `La pestana ${sheetName} no esta habilitada para FICHAS_SEM_CONTROL.`,
      'Actualizar ficha SEM',
      8
    );
    return;
  }

  fichasLanzarActualizacion_(clientKey, true, sheetName, sheetName);
}

// Funcion exclusiva de los dos activadores horarios. No depende de la hoja activa.
function FICHAS_10_actualizarTodasProgramado() {
  fichasLanzarActualizacion_(SEM_ALL_CLIENTS_VALUE, false);
}

function FICHAS_98_instalarActivadores() {
  const handlers = new Set([
    ...SEM_SCHEDULES.map((schedule) => schedule.handler),
    ...SEM_LEGACY_TRIGGER_HANDLERS,
  ]);

  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (handlers.has(trigger.getHandlerFunction())) {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  SEM_SCHEDULES.forEach((schedule) => {
    ScriptApp.newTrigger(schedule.handler)
      .timeBased()
      .atHour(schedule.hour)
      .nearMinute(schedule.minute)
      .everyDays(1)
      .inTimezone(SEM_TIME_ZONE)
      .create();
  });

  Logger.log(
    'Activadores SEM instalados: 07:35 y 12:50, zona Europe/Madrid.'
  );
}

function FICHAS_99_auditarActivadores() {
  const triggers = ScriptApp.getProjectTriggers();
  const current = triggers.filter(
    (trigger) =>
      trigger.getHandlerFunction() === SEM_SCHEDULED_HANDLER &&
      trigger.getEventType() === ScriptApp.EventType.CLOCK
  );
  const legacy = triggers
    .map((trigger) => trigger.getHandlerFunction())
    .filter((handler) => SEM_LEGACY_TRIGGER_HANDLERS.includes(handler));

  if (current.length !== SEM_SCHEDULES.length || legacy.length) {
    throw new Error(
      `FICHAS: se esperaban ${SEM_SCHEDULES.length} activadores nuevos y ` +
        `ninguno antiguo; nuevos=${current.length}, antiguos=${legacy.length}.`
    );
  }

  Logger.log(
    'FICHAS: auditoria correcta; hay dos activadores diarios con un unico handler.'
  );
}

function fichasLanzarActualizacion_(
  cliente,
  mostrarToast = true,
  displayLabel = null,
  controlSheetName = null
) {
  const label =
    displayLabel ||
    (cliente === SEM_ALL_CLIENTS_VALUE ? 'todas las fichas' : 'esta ficha');
  const lock = LockService.getScriptLock();

  if (!lock.tryLock(SEM_DISPATCH_LOCK_WAIT_MS)) {
    fichasNotificarDespacho_(
      'Ya hay otra actualizacion enviandose. Espera unos segundos.',
      mostrarToast
    );
    return false;
  }

  try {
    const properties = PropertiesService.getScriptProperties();
    const token = RuntimeConfig.required('GITHUB_TOKEN');
    const github = RuntimeConfig.github(
      'FICHAS_GITHUB_WORKFLOW',
      'actualizar-ficha-sem.yml'
    );

    const dispatchKey = `${SEM_DISPATCH_PROPERTY_PREFIX}${cliente}`;
    const now = Date.now();
    const lastDispatch = Number(properties.getProperty(dispatchKey) || 0);
    const cooldownMs = SEM_DISPATCH_COOLDOWN_SECONDS * 1000;

    if (mostrarToast && fichasTienePendiente_(properties, cliente)) {
      const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
      fichasActualizarEstado_(
        spreadsheet,
        controlSheetName || spreadsheet.getActiveSheet().getName(),
        'Actualizacion en curso',
        '#fbbc04'
      );
      fichasNotificarDespacho_(
        `Ya hay una actualizacion de ${label} en curso.`,
        mostrarToast
      );
      return false;
    }

    if (lastDispatch && now - lastDispatch < cooldownMs) {
      const remainingSeconds = Math.ceil(
        (cooldownMs - (now - lastDispatch)) / 1000
      );
      fichasNotificarDespacho_(
        `La actualizacion de ${label} ya fue solicitada. Para evitar ` +
          `duplicados, podras volver a solicitarla en ${remainingSeconds} ` +
          `segundos. Este contador no indica el tiempo que falta para que ` +
          `termine.`,
        mostrarToast
      );
      return false;
    }

    const url =
      `https://api.github.com/repos/${github.owner}/${github.repo}` +
      `/actions/workflows/${github.workflow}/dispatches`;
    const requestId = mostrarToast ? Utilities.getUuid() : '';

    if (mostrarToast) {
      fichasActualizarEstado_(
        SpreadsheetApp.getActiveSpreadsheet(),
        controlSheetName || SpreadsheetApp.getActiveSheet().getName(),
        'Actualizacion en curso',
        '#fbbc04'
      );
    }

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
          cliente: cliente,
          request_id: requestId,
        },
      }),
      muteHttpExceptions: true,
    });

    const responseCode = response.getResponseCode();
    if (responseCode !== 200 && responseCode !== 204) {
      throw new Error(
        `GitHub devolvio ${responseCode}: ${response.getContentText()}`
      );
    }

    properties.setProperty(dispatchKey, String(now));
    if (mostrarToast) {
      const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
      fichasRegistrarPendiente_(
        properties,
        requestId,
        spreadsheet.getId(),
        controlSheetName || spreadsheet.getActiveSheet().getName(),
        cliente,
        label,
        now
      );
    }
    fichasNotificarDespacho_(
      `Actualizacion de ${label} enviada. GitHub trabajara en segundo plano; ` +
        'la hoja avisara cuando termine.',
      mostrarToast
    );
    return true;
  } catch (error) {
    if (mostrarToast) {
      fichasActualizarEstado_(
        SpreadsheetApp.getActiveSpreadsheet(),
        controlSheetName || SpreadsheetApp.getActiveSheet().getName(),
        'Error al enviar',
        '#ff0000'
      );
      fichasNotificarDespacho_(
        `No se pudo enviar la actualizacion: ${error.message}`,
        mostrarToast
      );
    }
    throw error;
  } finally {
    lock.releaseLock();
  }
}

function FICHAS_03_comprobarFinalizaciones() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(SEM_DISPATCH_LOCK_WAIT_MS)) {
    return;
  }

  try {
    const properties = PropertiesService.getScriptProperties();
    const pendingKeys = properties
      .getKeys()
      .filter((key) => key.startsWith(SEM_PENDING_PROPERTY_PREFIX));
    if (!pendingKeys.length) {
      fichasEliminarActivadoresFinalizacion_();
      return;
    }

    const token = RuntimeConfig.required('GITHUB_TOKEN');
    const github = RuntimeConfig.github(
      'FICHAS_GITHUB_WORKFLOW',
      'actualizar-ficha-sem.yml'
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
      let pending;
      try {
        pending = JSON.parse(properties.getProperty(key));
      } catch (error) {
        properties.deleteProperty(key);
        return;
      }

      const run = runs.find((item) =>
        String(item.display_title || '').includes(pending.requestId)
      );
      const ageMinutes = (now - Number(pending.requestedAt)) / 60000;

      if (!run && ageMinutes <= SEM_COMPLETION_TIMEOUT_MINUTES) {
        return;
      }
      if (run && run.status !== 'completed') {
        return;
      }

      if (!run) {
        fichasFinalizarPendiente_(properties, key, pending, {
          conclusion: 'unconfirmed',
          runUrl: '',
          finishedAt: new Date(),
        });
      } else {
        fichasFinalizarPendiente_(properties, key, pending, {
          conclusion: run.conclusion || 'unknown',
          runUrl: run.html_url || '',
          finishedAt: new Date(run.updated_at),
        });
      }
    });

    const remaining = properties
      .getKeys()
      .some((key) => key.startsWith(SEM_PENDING_PROPERTY_PREFIX));
    if (!remaining) {
      fichasEliminarActivadoresFinalizacion_();
    }
  } finally {
    lock.releaseLock();
  }
}

function fichasFinalizarPendiente_(
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

  if (conclusion === 'success') {
    const finishedLabel = Utilities.formatDate(
      finishedAt,
      SEM_TIME_ZONE,
      'dd/MM/yyyy HH:mm'
    );
    fichasActualizarEstado_(
      spreadsheet,
      pending.sheetName,
      `Actualizado ${finishedLabel}`,
      '#34a853',
      runUrl
    );
    spreadsheet.toast(
      `Actualizacion finalizada correctamente: ${pending.label}.`,
      'Ficha SEM actualizada',
      10
    );
  } else if (conclusion === 'unconfirmed') {
    fichasActualizarEstado_(
      spreadsheet,
      pending.sheetName,
      'Sin confirmacion de GitHub',
      '#ff0000'
    );
    spreadsheet.toast(
      'No se pudo confirmar la finalizacion. Revisa GitHub Actions.',
      'Actualizar ficha SEM',
      10
    );
  } else {
    fichasActualizarEstado_(
      spreadsheet,
      pending.sheetName,
      `Error en GitHub: ${conclusion}`,
      '#ff0000',
      runUrl
    );
    spreadsheet.toast(
      `La actualizacion de ${pending.label} termino con error. ` +
        'Revisa GitHub Actions.',
      'Error al actualizar ficha SEM',
      10
    );
  }

  properties.deleteProperty(pendingKey);
}

function fichasRegistrarPendiente_(
  properties,
  requestId,
  spreadsheetId,
  sheetName,
  cliente,
  label,
  requestedAt
) {
  properties.setProperty(
    `${SEM_PENDING_PROPERTY_PREFIX}${requestId}`,
    JSON.stringify({
      requestId: requestId,
      spreadsheetId: spreadsheetId,
      sheetName: sheetName,
      cliente: cliente,
      label: label,
      requestedAt: requestedAt,
    })
  );

  const triggerExists = ScriptApp.getProjectTriggers().some(
    (trigger) => trigger.getHandlerFunction() === SEM_COMPLETION_HANDLER
  );
  if (!triggerExists) {
    ScriptApp.newTrigger(SEM_COMPLETION_HANDLER)
      .timeBased()
      .everyMinutes(1)
      .create();
  }
}

function fichasTienePendiente_(properties, cliente) {
  return properties
    .getKeys()
    .filter((key) => key.startsWith(SEM_PENDING_PROPERTY_PREFIX))
    .some((key) => {
      try {
        const pending = JSON.parse(properties.getProperty(key));
        return (
          cliente === SEM_ALL_CLIENTS_VALUE ||
          pending.cliente === SEM_ALL_CLIENTS_VALUE ||
          pending.cliente === cliente
        );
      } catch (error) {
        properties.deleteProperty(key);
        return false;
      }
    });
}

function fichasEliminarActivadoresFinalizacion_() {
  ScriptApp.getProjectTriggers()
    .filter(
      (trigger) => trigger.getHandlerFunction() === SEM_COMPLETION_HANDLER
    )
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
}

function fichasActualizarEstado_(
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
  const controlColumn =
    fichasBuscarColumnaControl_(sheet) || SEM_BUTTON_COLUMN;
  const statusColumn = controlColumn + 1;
  const status = sheet.getRange(SEM_STATUS_ROW, statusColumn);
  status
    .setValue(message)
    .setNote(runUrl ? `Ejecucion: ${runUrl}` : '')
    .setFontColor(color)
    .setFontWeight('bold')
    .setFontSize(8)
    .setHorizontalAlignment('left')
    .setVerticalAlignment('middle');

  // Si el control se ha movido manualmente, elimina solo los estados antiguos
  // gestionados por este script para que no aparezcan dos mensajes distintos.
  const rowValues = sheet
    .getRange(SEM_STATUS_ROW, 1, 1, sheet.getMaxColumns())
    .getDisplayValues()[0];
  rowValues.forEach((value, index) => {
    const column = index + 1;
    if (column !== statusColumn && fichasEsEstadoGestionado_(value)) {
      sheet
        .getRange(SEM_STATUS_ROW, column)
        .clearContent()
        .clearNote()
        .setFontWeight('normal');
    }
  });
}

function fichasNotificarDespacho_(message, mostrarToast) {
  Logger.log(message);
  if (mostrarToast) {
    SpreadsheetApp.getActiveSpreadsheet().toast(
      message,
      'Actualizar ficha SEM',
      6
    );
  }
}

function FICHAS_90_repararBotones() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const missingSheets = [];
  const results = [];
  const worksheetNames = Object.keys(fichasWorksheetMap_());
  const globalSheetName = RuntimeConfig.required('SEM_GLOBAL_SHEET');

  worksheetNames.forEach((sheetName) => {
    const sheet = spreadsheet.getSheetByName(sheetName);
    if (!sheet) {
      missingSheets.push(sheetName);
      return;
    }

    sheet.getDrawings().forEach((drawing) => {
      if (fichasLegacyButtonHandlers_().includes(drawing.getOnAction())) {
        drawing.remove();
      }
    });
    results.push(fichasRepararBotonEnHoja_(sheet, 'Actualizar ficha SEM'));
  });

  const globalSheet = spreadsheet.getSheetByName(globalSheetName);
  if (!globalSheet) {
    missingSheets.push(globalSheetName);
  } else {
    results.push(
      fichasRepararBotonEnHoja_(globalSheet, 'Actualizar todas las fichas')
    );
  }

  if (missingSheets.length) {
    throw new Error(
      `No se encontraron estas pestanas: ${missingSheets.join(', ')}`
    );
  }

  ScriptApp.getProjectTriggers()
    .filter(
      (trigger) =>
        trigger.getHandlerFunction() === SEM_MANUAL_EDIT_HANDLER
    )
    .forEach((trigger) => ScriptApp.deleteTrigger(trigger));
  ScriptApp.newTrigger(SEM_MANUAL_EDIT_HANDLER)
    .forSpreadsheet(spreadsheet.getId())
    .onEdit()
    .create();

  const installed = results.filter((result) => result === 'instalado').length;
  const message =
    `FICHAS: ${installed} controles de casilla instalados y un activador ` +
    'manual configurado.';
  Logger.log(message);
  spreadsheet.toast(message, 'Controles FICHAS_SEM_CONTROL', 8);
}

function fichasRepararBotonEnHoja_(sheet, label) {
  const existingControlColumn = fichasBuscarColumnaControl_(sheet);
  const controlColumn = existingControlColumn || SEM_BUTTON_COLUMN;
  const statusColumn = controlColumn + 1;
  const requiredColumn = Math.max(statusColumn, SEM_STATUS_COLUMN);
  if (sheet.getMaxColumns() < requiredColumn) {
    sheet.insertColumnsAfter(
      sheet.getMaxColumns(),
      requiredColumn - sheet.getMaxColumns()
    );
  }

  sheet.getImages()
    .filter((image) => image.getAltTextTitle() === SEM_BUTTON_MARKER)
    .forEach((image) => image.remove());

  const legacyCheckbox = sheet.getRange(SEM_BUTTON_ROW, 17);
  if (legacyCheckbox.getNote() === SEM_CONTROL_MARKER) {
    legacyCheckbox
      .removeCheckboxes()
      .clearContent()
      .clearNote()
      .setBackground(null)
      .setBorder(false, false, false, false, false, false);
  }
  const legacyStatus = sheet.getRange(SEM_STATUS_ROW, 18);
  if (
    /^(Listo|Actualizacion|Actualizado|Error|Sin confirmacion)/.test(
      String(legacyStatus.getValue() || '')
    )
  ) {
    legacyStatus.clearContent().clearNote().setFontWeight('normal');
  }

  const checkbox = sheet.getRange(SEM_BUTTON_ROW, controlColumn);
  checkbox
    .insertCheckboxes()
    .setValue(false)
    .setNote(SEM_CONTROL_MARKER)
    .setBackground('#1a73e8')
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setBorder(
      true,
      true,
      true,
      false,
      false,
      false,
      '#1557b0',
      SpreadsheetApp.BorderStyle.SOLID_MEDIUM
    );

  sheet
    .getRange(SEM_BUTTON_ROW, controlColumn + 1)
    .setValue(label)
    .setNote('Marca la casilla azul para iniciar la actualizacion.')
    .setBackground('#1a73e8')
    .setFontColor('#ffffff')
    .setFontWeight('bold')
    .setFontSize(9)
    .setHorizontalAlignment('center')
    .setVerticalAlignment('middle')
    .setBorder(
      true,
      false,
      true,
      true,
      false,
      false,
      '#1557b0',
      SpreadsheetApp.BorderStyle.SOLID_MEDIUM
    );
  sheet.setColumnWidth(controlColumn, 32);
  sheet.setColumnWidth(controlColumn + 1, 150);
  sheet.setRowHeight(SEM_BUTTON_ROW, 28);

  const status = sheet.getRange(SEM_STATUS_ROW, statusColumn);
  const fixedStatus = sheet.getRange(SEM_STATUS_ROW, SEM_STATUS_COLUMN);
  if (
    statusColumn !== SEM_STATUS_COLUMN &&
    fichasEsEstadoGestionado_(fixedStatus.getDisplayValue())
  ) {
    status
      .setValue(fixedStatus.getValue())
      .setNote(fixedStatus.getNote())
      .setFontColor(fixedStatus.getFontColor())
      .setFontWeight(fixedStatus.getFontWeight())
      .setFontSize(fixedStatus.getFontSize())
      .setHorizontalAlignment('left')
      .setVerticalAlignment('middle');
    fixedStatus
      .clearContent()
      .clearNote()
      .setFontWeight('normal');
  }
  if (!String(status.getValue() || '').trim()) {
    status
      .setValue('Listo para actualizar')
      .setFontColor('#5f6368')
      .setFontWeight('bold')
      .setFontSize(8)
      .setHorizontalAlignment('left')
      .setVerticalAlignment('middle');
  }
  return 'instalado';
}

function FICHAS_91_auditarBotones() {
  const spreadsheet = SpreadsheetApp.getActiveSpreadsheet();
  const errors = [];
  const worksheetNames = Object.keys(fichasWorksheetMap_());
  const globalSheetName = RuntimeConfig.required('SEM_GLOBAL_SHEET');

  worksheetNames.forEach((sheetName) => {
    const sheet = spreadsheet.getSheetByName(sheetName);
    if (!sheet) {
      errors.push(`${sheetName}: pestana ausente`);
      return;
    }

    const managedButtons = sheet.getImages().filter(
      (image) => image.getAltTextTitle() === SEM_BUTTON_MARKER
    );
    if (managedButtons.length) {
      errors.push(`${sheetName}: conserva ${managedButtons.length} imagenes`);
    }

    if (!fichasBuscarColumnaControl_(sheet)) {
      errors.push(`${sheetName}: casilla ausente o incorrecta`);
    }

    const legacyDrawings = sheet.getDrawings().filter((drawing) =>
      fichasLegacyButtonHandlers_().includes(drawing.getOnAction())
    ).length;
    if (legacyDrawings) {
      errors.push(`${sheetName}: ${legacyDrawings} dibujos antiguos`);
    }
  });

  const globalSheet = spreadsheet.getSheetByName(globalSheetName);
  if (!globalSheet) {
    errors.push(`${globalSheetName}: pestana ausente`);
  } else {
    const globalButtons = globalSheet.getImages().filter(
      (image) => image.getAltTextTitle() === SEM_BUTTON_MARKER
    );
    if (globalButtons.length) {
      errors.push(
        `${globalSheetName}: conserva ${globalButtons.length} imagenes`
      );
    }
    if (!fichasBuscarColumnaControl_(globalSheet)) {
      errors.push(`${globalSheetName}: casilla ausente o incorrecta`);
    }
  }

  const triggerCount = ScriptApp.getProjectTriggers().filter(
    (trigger) =>
      trigger.getHandlerFunction() === SEM_MANUAL_EDIT_HANDLER &&
      trigger.getEventType() === ScriptApp.EventType.ON_EDIT &&
      trigger.getTriggerSourceId() === spreadsheet.getId()
  ).length;
  if (triggerCount !== 1) {
    errors.push(`activadores manuales: ${triggerCount}`);
  }

  if (errors.length) {
    throw new Error(`FICHAS: auditoria de botones incorrecta: ${errors.join('; ')}`);
  }

  Logger.log(
    `FICHAS: auditoria correcta; ${worksheetNames.length} casillas individuales ` +
      'y una casilla general usan el activador manual.'
  );
}
