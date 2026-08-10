const CALENDAR_SEM_TIME_ZONE = 'Europe/Madrid';
const CALENDAR_SEM_TRIGGER_HANDLER =
  'CALENDARIO_01_verificarAyerProgramado';
const CALENDAR_SEM_LEGACY_TRIGGER_HANDLERS = [
  'ejecutarVerificacionCalendarioSem',
];
const CALENDAR_SEM_TRIGGER_HOUR = 7;
const CALENDAR_SEM_TRIGGER_MINUTE = 50;
const CALENDAR_SEM_LOCK_WAIT_MS = 5000;

/** Funcion productiva llamada cada dia por el activador horario. */
function CALENDARIO_01_verificarAyerProgramado() {
  calendarioLanzarVerificacion_(false);
}

/** Lanza GitHub en dry-run para probar el flujo sin enviar correos. */
function CALENDARIO_90_probarAyerSinEmail() {
  calendarioLanzarVerificacion_(true);
}

/** Reinstala un unico activador diario, cerca de las 07:50 de Madrid. */
function CALENDARIO_98_instalarActivador() {
  const managedHandlers = new Set([
    CALENDAR_SEM_TRIGGER_HANDLER,
    ...CALENDAR_SEM_LEGACY_TRIGGER_HANDLERS,
  ]);
  ScriptApp.getProjectTriggers().forEach((trigger) => {
    if (managedHandlers.has(trigger.getHandlerFunction())) {
      ScriptApp.deleteTrigger(trigger);
    }
  });

  ScriptApp.newTrigger(CALENDAR_SEM_TRIGGER_HANDLER)
    .timeBased()
    .atHour(CALENDAR_SEM_TRIGGER_HOUR)
    .nearMinute(CALENDAR_SEM_TRIGGER_MINUTE)
    .everyDays(1)
    .inTimezone(CALENDAR_SEM_TIME_ZONE)
    .create();

  Logger.log(
    'Activador Calendar SEM instalado cerca de las 07:50, zona Europe/Madrid.'
  );
}

/** Comprueba que existe exactamente un activador productivo. */
function CALENDARIO_99_auditarActivador() {
  const triggers = ScriptApp.getProjectTriggers().filter(
    (trigger) =>
      trigger.getHandlerFunction() === CALENDAR_SEM_TRIGGER_HANDLER &&
      trigger.getEventType() === ScriptApp.EventType.CLOCK
  );
  const legacy = ScriptApp.getProjectTriggers()
    .map((trigger) => trigger.getHandlerFunction())
    .filter((handler) =>
      CALENDAR_SEM_LEGACY_TRIGGER_HANDLERS.includes(handler)
    );

  if (triggers.length !== 1 || legacy.length) {
    throw new Error(
      `CALENDARIO: se esperaba 1 activador nuevo y ninguno antiguo; ` +
        `nuevos=${triggers.length}, antiguos=${legacy.length}.`
    );
  }

  Logger.log(
    'Auditoria correcta: 1 activador Calendar SEM cerca de las 07:50.'
  );
}

function calendarioLanzarVerificacion_(dryRun) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(CALENDAR_SEM_LOCK_WAIT_MS)) {
    throw new Error('Ya se esta enviando otra verificacion de Calendar.');
  }

  try {
    const token = RuntimeConfig.required('GITHUB_TOKEN');
    const github = RuntimeConfig.github(
      'CALENDAR_GITHUB_WORKFLOW',
      'verificar-acciones-calendario-sem.yml'
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
        inputs: { dry_run: String(Boolean(dryRun)) },
      }),
      muteHttpExceptions: true,
    });

    const status = response.getResponseCode();
    if (status !== 204) {
      throw new Error(
        `GitHub devolvio ${status}: ${response.getContentText()}`
      );
    }

    Logger.log(
      `Workflow Calendar SEM lanzado correctamente. dry_run=${Boolean(dryRun)}`
    );
  } finally {
    lock.releaseLock();
  }
}
