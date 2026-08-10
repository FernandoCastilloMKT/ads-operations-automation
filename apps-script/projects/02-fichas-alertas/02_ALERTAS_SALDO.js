const SEM_SALDO_SUBJECT = 'Alerta SEM: clientes con saldo negativo';
const SEM_SALDO_TIME_ZONE = 'Europe/Madrid';
const SEM_SALDO_STATE_PROPERTY = 'SEM_SALDO_ALERTAS_V1';
const SEM_SALDO_TRIGGER_HANDLER = 'SALDOS_01_revisarYEnviarAlertas';
const SEM_SALDO_LEGACY_TRIGGER_HANDLERS = ['ejecutarAlertasSaldoSem'];
const SEM_SALDO_LOCK_WAIT_MS = 10000;
const SEM_SALDO_RENOTIFY_DELTA_EUR = 10;
const SEM_SALDO_SEND_EMAIL = true;
const SEM_SALDO_SCHEDULES = [
  { hour: 7, minute: 55, label: '07:55' },
];
const SEM_SALDO_EXCLUDED_SECTIONS = [
  'clientes firmados sin comenzar',
  'stand by',
  'standby',
  'natalia',
];

const SEM_SALDO_HEADER_ALIASES = {
  estado: ['estado'],
  idCuenta: ['id cuenta', 'id de cuenta'],
  cliente: ['cliente'],
  fechaInicio: ['f inicio', 'fecha inicio'],
  fechaFin: ['f fin sem', 'fecha fin sem', 'fecha fin'],
  numeroMes: ['n mes', 'numero mes'],
  ptoTotal: ['pto total', 'presupuesto total', 'presup total'],
  ptoMes: ['pto mes', 'presupuesto mes', 'presup mes'],
  restoSaldo: ['resto saldo', 'saldo restante'],
  alertaFechaFin: ['alerta f fin', 'alerta fecha fin'],
  diasRestantes: ['res dias', 'dias restantes'],
  comercial: ['comercial'],
  tecnico: ['tecnico'],
};

const SEM_SALDO_EMAIL_COLUMNS = [
  { key: 'estado', label: 'Estado' },
  { key: 'idCuenta', label: 'ID Cuenta' },
  { key: 'cliente', label: 'Cliente' },
  { key: 'fechaInicio', label: 'F. Inicio' },
  { key: 'fechaFin', label: 'F. Fin Sem' },
  { key: 'numeroMes', label: 'N\u00ba Mes' },
  { key: 'ptoTotal', label: 'Pto. Total', align: 'right' },
  { key: 'ptoMesDisplay', label: 'Pto. Mes', align: 'right' },
  {
    key: 'restoSaldoDisplay',
    label: 'Resto Saldo',
    align: 'right',
    alert: true,
  },
  { key: 'alertaFechaFin', label: 'Alerta F.Fin' },
  { key: 'diasRestantes', label: 'Res. d\u00edas', align: 'right' },
  { key: 'comercial', label: 'Comercial' },
  { key: 'tecnico', label: 'T\u00e9cnico' },
];

/** Reads Vista Global and sends one summary email for changed alerts. */
function SALDOS_01_revisarYEnviarAlertas() {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(SEM_SALDO_LOCK_WAIT_MS)) {
    Logger.log('Alertas saldo SEM: otra ejecucion sigue activa.');
    return;
  }

  try {
    const result = semSaldoCollectAlerts_();
    const previousState = semSaldoReadState_();
    const currentKeys = new Set(result.alerts.map((alert) => alert.accountKey));
    const nextState = {};

    Object.keys(previousState).forEach((accountKey) => {
      if (currentKeys.has(accountKey)) {
        nextState[accountKey] = previousState[accountKey];
      }
    });

    const pendingAlerts = result.alerts.filter((alert) => {
      const previous = previousState[alert.accountKey];
      return semSaldoShouldNotify_(alert, previous);
    });

    Logger.log(
      `Alertas saldo SEM: ${result.rowsRead} filas leidas, ` +
        `${result.alerts.length} alertas activas y ` +
        `${pendingAlerts.length} alertas nuevas o modificadas.`
    );

    if (!pendingAlerts.length) {
      semSaldoWriteState_(nextState);
      Logger.log('Alertas saldo SEM: no hay avisos nuevos; no se envia email.');
      return;
    }

    const email = semSaldoBuildEmail_(pendingAlerts, result.checkedAt);
    if (!SEM_SALDO_SEND_EMAIL) {
      Logger.log('Alertas saldo SEM: envio desactivado. Vista previa:');
      Logger.log(email.plainBody);
      return;
    }

    MailApp.sendEmail({
      to: RuntimeConfig.list('SEM_SALDO_RECIPIENTS').join(','),
      subject: SEM_SALDO_SUBJECT,
      body: email.plainBody,
      htmlBody: email.htmlBody,
      name: 'Alertas SEM',
    });

    const notifiedAt = new Date().toISOString();
    pendingAlerts.forEach((alert) => {
      nextState[alert.accountKey] = {
        notifiedBalance: alert.restoSaldo,
        rule: alert.rule,
        notifiedAt: notifiedAt,
      };
    });
    semSaldoWriteState_(nextState);

    Logger.log(
      `Alertas saldo SEM: email enviado a ${RuntimeConfig.list('SEM_SALDO_RECIPIENTS').join(', ')} ` +
        `con ${pendingAlerts.length} clientes.`
    );
  } finally {
    lock.releaseLock();
  }
}

/** Logs the real email preview without sending it or changing state. */
function SALDOS_90_previsualizarSinEnviar() {
  const result = semSaldoCollectAlerts_();
  const previousState = semSaldoReadState_();
  const pendingAlerts = result.alerts.filter((alert) => {
    const previous = previousState[alert.accountKey];
    return semSaldoShouldNotify_(alert, previous);
  });
  const email = semSaldoBuildEmail_(result.alerts, result.checkedAt);

  Logger.log(
    `PRUEBA alertas saldo SEM: ${result.rowsRead} filas leidas, ` +
      `${result.alerts.length} alertas activas y ` +
      `${pendingAlerts.length} pendientes segun PropertiesService.`
  );
  Logger.log(email.plainBody);
}

/** Sends a real test email without reading or changing the anti-spam state. */
function SALDOS_91_enviarPruebaReal() {
  const result = semSaldoCollectAlerts_();
  if (!result.alerts.length) {
    Logger.log('PRUEBA alertas saldo SEM: no hay alertas; no se envia email.');
    return;
  }

  const email = semSaldoBuildEmail_(result.alerts, result.checkedAt);
  MailApp.sendEmail({
    to: RuntimeConfig.list('SEM_SALDO_RECIPIENTS').join(','),
    subject: SEM_SALDO_SUBJECT,
    body: email.plainBody,
    htmlBody: email.htmlBody,
    name: 'Alertas SEM',
  });

  Logger.log(
    `PRUEBA alertas saldo SEM: email enviado a ${RuntimeConfig.list('SEM_SALDO_RECIPIENTS').join(
      ', '
    )} ` +
      `con ${result.alerts.length} clientes; estado anti-spam sin cambios.`
  );
}

/** Installs the configured cloud triggers in the Europe/Madrid time zone. */
function SALDOS_98_instalarActivador() {
  const managedHandlers = new Set([
    SEM_SALDO_TRIGGER_HANDLER,
    ...SEM_SALDO_LEGACY_TRIGGER_HANDLERS,
  ]);
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (managedHandlers.has(trigger.getHandlerFunction())) {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  SEM_SALDO_SCHEDULES.forEach((schedule) => {
    ScriptApp.newTrigger(SEM_SALDO_TRIGGER_HANDLER)
      .timeBased()
      .atHour(schedule.hour)
      .nearMinute(schedule.minute)
      .everyDays(1)
      .inTimezone(SEM_SALDO_TIME_ZONE)
      .create();
  });

  Logger.log(
    `Alertas saldo SEM: activadores instalados para ${SEM_SALDO_SCHEDULES.map(
      (schedule) => schedule.label
    ).join(' y ')}, zona ${SEM_SALDO_TIME_ZONE}.`
  );
}

/** Verifies the number of configured clock triggers for the alert handler. */
function SALDOS_99_auditarActivador() {
  const triggers = ScriptApp.getProjectTriggers().filter(
    (trigger) =>
      trigger.getHandlerFunction() === SEM_SALDO_TRIGGER_HANDLER &&
      trigger.getEventType() === ScriptApp.EventType.CLOCK
  );
  const legacy = ScriptApp.getProjectTriggers()
    .map((trigger) => trigger.getHandlerFunction())
    .filter((handler) => SEM_SALDO_LEGACY_TRIGGER_HANDLERS.includes(handler));

  if (triggers.length !== SEM_SALDO_SCHEDULES.length || legacy.length) {
    throw new Error(
      `Se esperaban ${SEM_SALDO_SCHEDULES.length} activadores de saldo SEM ` +
        `y se encontraron ${triggers.length}; antiguos=${legacy.length}.`
    );
  }

  Logger.log(
    `Alertas saldo SEM: auditoria correcta; hay ${triggers.length} ` +
      'activador horario.'
  );
}

function semSaldoCollectAlerts_() {
  const spreadsheet = SpreadsheetApp.openById(RuntimeConfig.required('CONTROL_SEM_SPREADSHEET_ID'));
  const worksheet = spreadsheet.getSheetByName(RuntimeConfig.required('SEM_GLOBAL_SHEET'));
  if (!worksheet) {
    throw new Error(`No existe la pestana ${RuntimeConfig.required('SEM_GLOBAL_SHEET')}.`);
  }

  const lastRow = worksheet.getLastRow();
  const lastColumn = worksheet.getLastColumn();
  if (!lastRow || !lastColumn) {
    throw new Error(`${RuntimeConfig.required('SEM_GLOBAL_SHEET')} esta vacia.`);
  }

  const range = worksheet.getRange(1, 1, lastRow, lastColumn);
  const values = range.getValues();
  const displayValues = range.getDisplayValues();
  const header = semSaldoFindHeader_(displayValues);
  const alertsByAccount = {};
  let excludedSection = '';
  let excludedRows = 0;

  for (let rowIndex = header.rowIndex + 1; rowIndex < values.length; rowIndex += 1) {
    const row = values[rowIndex];
    const displayRow = displayValues[rowIndex];
    const client = semSaldoCellText_(displayRow, header.columns.cliente);
    const displayedId = semSaldoCellText_(displayRow, header.columns.idCuenta);
    const accountKey = displayedId.replace(/\D/g, '');

    if (semSaldoIsSectionMarker_(client, displayedId, accountKey)) {
      const sectionName = semSaldoNormalizeHeader_(displayedId);
      excludedSection = SEM_SALDO_EXCLUDED_SECTIONS.includes(sectionName)
        ? sectionName
        : '';
      if (excludedSection) {
        Logger.log(
          `Alertas saldo SEM: se excluye el bloque ${displayedId} desde la ` +
            `fila ${rowIndex + 1}.`
        );
      }
      continue;
    }

    if (excludedSection) {
      excludedRows += 1;
      continue;
    }

    if (!client || !/^\d{10}$/.test(accountKey)) {
      continue;
    }

    const monthlyBudget = semSaldoParseMoney_(
      row[header.columns.ptoMes],
      displayRow[header.columns.ptoMes]
    );
    const remainingBalance = semSaldoParseMoney_(
      row[header.columns.restoSaldo],
      displayRow[header.columns.restoSaldo]
    );

    if (monthlyBudget === null || remainingBalance === null) {
      Logger.log(
        `Alertas saldo SEM: fila ${rowIndex + 1} ignorada por importe no valido ` +
          `(${client}).`
      );
      continue;
    }

    const rule = semSaldoEvaluateRule_(monthlyBudget, remainingBalance);
    if (!rule) {
      continue;
    }

    const alert = {
      rowNumber: rowIndex + 1,
      accountKey: accountKey,
      idCuenta: displayedId,
      cliente: client,
      estado: semSaldoCellText_(displayRow, header.columns.estado),
      fechaInicio: semSaldoCellText_(displayRow, header.columns.fechaInicio),
      fechaFin: semSaldoCellText_(displayRow, header.columns.fechaFin),
      numeroMes: semSaldoCellText_(displayRow, header.columns.numeroMes),
      ptoTotal: semSaldoCellText_(displayRow, header.columns.ptoTotal),
      ptoMes: monthlyBudget,
      ptoMesDisplay: semSaldoCellText_(displayRow, header.columns.ptoMes),
      restoSaldo: remainingBalance,
      restoSaldoDisplay: semSaldoCellText_(
        displayRow,
        header.columns.restoSaldo
      ),
      alertaFechaFin: semSaldoCellText_(
        displayRow,
        header.columns.alertaFechaFin
      ),
      diasRestantes: semSaldoCellText_(displayRow, header.columns.diasRestantes),
      comercial: semSaldoCellText_(displayRow, header.columns.comercial),
      tecnico: semSaldoCellText_(displayRow, header.columns.tecnico),
      rule: rule,
    };

    const previous = alertsByAccount[accountKey];
    if (!previous || alert.restoSaldo < previous.restoSaldo) {
      alertsByAccount[accountKey] = alert;
    }
  }

  const alerts = Object.keys(alertsByAccount)
    .map((accountKey) => alertsByAccount[accountKey])
    .sort((left, right) => left.restoSaldo - right.restoSaldo);

  Logger.log(
    `Alertas saldo SEM: libro abierto, pestana ${RuntimeConfig.required('SEM_GLOBAL_SHEET')}, ` +
      `cabecera detectada en fila ${header.rowIndex + 1}; ` +
      `${excludedRows} filas de bloques excluidos.`
  );

  return {
    alerts: alerts,
    rowsRead: Math.max(0, values.length - header.rowIndex - 1),
    checkedAt: new Date(),
  };
}

function semSaldoIsSectionMarker_(client, displayedId, accountKey) {
  return !client && Boolean(displayedId) && !/^\d{10}$/.test(accountKey);
}

function semSaldoFindHeader_(displayValues) {
  const maxRows = Math.min(displayValues.length, 20);
  const requiredKeys = Object.keys(SEM_SALDO_HEADER_ALIASES);

  for (let rowIndex = 0; rowIndex < maxRows; rowIndex += 1) {
    const normalizedCells = displayValues[rowIndex].map(semSaldoNormalizeHeader_);
    const columns = {};

    requiredKeys.forEach((key) => {
      const aliases = SEM_SALDO_HEADER_ALIASES[key];
      const columnIndex = normalizedCells.findIndex((value) =>
        aliases.includes(value)
      );
      if (columnIndex >= 0) {
        columns[key] = columnIndex;
      }
    });

    if (requiredKeys.every((key) => Object.prototype.hasOwnProperty.call(columns, key))) {
      return { rowIndex: rowIndex, columns: columns };
    }
  }

  throw new Error(
    `No se encontro una cabecera valida en ${RuntimeConfig.required('SEM_GLOBAL_SHEET')}. ` +
      `Cabeceras requeridas: ${requiredKeys.join(', ')}.`
  );
}

function semSaldoNormalizeHeader_(value) {
  return String(value || '')
    .normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '')
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, ' ')
    .trim();
}

function semSaldoCellText_(row, columnIndex) {
  if (columnIndex < 0 || columnIndex >= row.length) {
    return '';
  }
  return String(row[columnIndex] || '').trim();
}

function semSaldoParseMoney_(rawValue, displayValue) {
  if (typeof rawValue === 'number' && Number.isFinite(rawValue)) {
    return rawValue;
  }

  const candidates = [rawValue, displayValue];
  for (let index = 0; index < candidates.length; index += 1) {
    let text = String(candidates[index] == null ? '' : candidates[index])
      .replace(/\u00a0/g, ' ')
      .trim();
    if (!text) {
      continue;
    }

    const parenthesizedNegative = /^\(.*\)$/.test(text);
    text = text.replace(/[^0-9,.-]/g, '');
    if (!text || text === '-') {
      continue;
    }

    if (text.includes(',')) {
      text = text.replace(/\./g, '').replace(',', '.');
    } else if (/^-?\d{1,3}(\.\d{3})+$/.test(text)) {
      text = text.replace(/\./g, '');
    }

    const parsed = Number(text);
    if (Number.isFinite(parsed)) {
      return parenthesizedNegative ? -Math.abs(parsed) : parsed;
    }
  }

  return null;
}

function semSaldoEvaluateRule_(monthlyBudget, remainingBalance) {
  if (monthlyBudget > 1500 && remainingBalance <= -125) {
    return 3;
  }
  if (
    monthlyBudget >= 999 &&
    monthlyBudget <= 1500 &&
    remainingBalance <= -75
  ) {
    return 1;
  }
  if (monthlyBudget < 999 && remainingBalance <= -30) {
    return 2;
  }
  return 0;
}

function semSaldoShouldNotify_(alert, previousState) {
  if (!previousState) {
    return true;
  }

  const previousBalance = Number.isFinite(previousState.notifiedBalance)
    ? previousState.notifiedBalance
    : previousState.roundedBalance;
  if (!Number.isFinite(previousBalance)) {
    return true;
  }

  return (
    Math.abs(alert.restoSaldo - previousBalance) >=
    SEM_SALDO_RENOTIFY_DELTA_EUR
  );
}

function semSaldoReadState_() {
  const raw = PropertiesService.getScriptProperties().getProperty(
    SEM_SALDO_STATE_PROPERTY
  );
  if (!raw) {
    return {};
  }

  try {
    const parsed = JSON.parse(raw);
    return parsed && typeof parsed === 'object' ? parsed : {};
  } catch (error) {
    Logger.log(
      `Alertas saldo SEM: estado anti-spam invalido; se reinicia (${error}).`
    );
    return {};
  }
}

function semSaldoWriteState_(state) {
  PropertiesService.getScriptProperties().setProperty(
    SEM_SALDO_STATE_PROPERTY,
    JSON.stringify(state)
  );
}

function semSaldoBuildEmail_(alerts, checkedAt) {
  const timestamp = Utilities.formatDate(
    checkedAt,
    SEM_SALDO_TIME_ZONE,
    'dd/MM/yyyy HH:mm'
  );
  const headerCells = SEM_SALDO_EMAIL_COLUMNS.map(
    (column) =>
      `<th style="padding:7px;border:1px solid #d0d7de;white-space:nowrap">` +
      `${semSaldoEscapeHtml_(column.label)}</th>`
  ).join('');
  const rows = alerts
    .map((alert) => {
      const cells = SEM_SALDO_EMAIL_COLUMNS.map((column) => {
        const styles = [
          'padding:7px',
          'border:1px solid #d0d7de',
          'white-space:nowrap',
        ];
        if (column.align) {
          styles.push(`text-align:${column.align}`);
        }
        if (column.alert) {
          styles.push('color:#b91c1c', 'font-weight:700');
        }
        return (
          `<td style="${styles.join(';')}">` +
          `${semSaldoEscapeHtml_(alert[column.key] || '-')}</td>`
        );
      }).join('');
      return `<tr>${cells}</tr>`;
    })
    .join('');

  const htmlBody = `
    <div style="font-family:Arial,sans-serif;color:#202124">
      <h2 style="margin-bottom:6px">Clientes SEM con saldo negativo</h2>
      <p style="margin-top:0;color:#5f6368">
        Revision de Vista Global: ${timestamp} (${SEM_SALDO_TIME_ZONE}).
      </p>
      <div style="overflow-x:auto">
      <table style="border-collapse:collapse;font-size:12px">
        <thead>
          <tr style="background:#1f4e78;color:white;text-align:left">
            ${headerCells}
          </tr>
        </thead>
        <tbody>${rows}</tbody>
      </table>
      </div>
      <p style="font-size:12px;color:#5f6368;margin-top:14px">
        Regla 1: Pto. Mes entre 999 y 1.500 EUR y saldo &lt;= -75 EUR.<br>
        Regla 2: Pto. Mes &lt; 999 EUR y saldo &lt;= -30 EUR.<br>
        Regla 3: Pto. Mes &gt; 1.500 EUR y saldo &lt;= -125 EUR.
      </p>
    </div>`;

  const plainHeader = SEM_SALDO_EMAIL_COLUMNS.map((column) => column.label).join(
    ' | '
  );
  const plainRows = alerts.map((alert) =>
    SEM_SALDO_EMAIL_COLUMNS.map((column) => alert[column.key] || '-').join(' | ')
  );
  const plainBody = [
    'Clientes SEM con saldo negativo',
    `Revision de Vista Global: ${timestamp} (${SEM_SALDO_TIME_ZONE}).`,
    '',
    plainHeader,
    ...plainRows,
    '',
    'Regla 1: Pto. Mes entre 999 y 1.500 EUR y saldo <= -75 EUR.',
    'Regla 2: Pto. Mes < 999 EUR y saldo <= -30 EUR.',
    'Regla 3: Pto. Mes > 1.500 EUR y saldo <= -125 EUR.',
  ].join('\n');

  return { htmlBody: htmlBody, plainBody: plainBody };
}

function semSaldoEscapeHtml_(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}
