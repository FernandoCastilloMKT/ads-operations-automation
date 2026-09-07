const RuntimeConfig = (() => {
  const properties = () => PropertiesService.getScriptProperties();

  const required = (name) => {
    const value = properties().getProperty(name);
    if (!value) {
      throw new Error(`Falta la propiedad privada ${name} en Apps Script.`);
    }
    return value;
  };

  const optional = (name, fallback) =>
    properties().getProperty(name) || fallback;

  const json = (name) => {
    try {
      return JSON.parse(required(name));
    } catch (error) {
      throw new Error(`La propiedad privada ${name} no contiene JSON valido.`);
    }
  };

  const optionalJson = (name, fallback) => {
    const value = properties().getProperty(name);
    if (!value) {
      return fallback;
    }
    try {
      return JSON.parse(value);
    } catch (error) {
      throw new Error(`La propiedad privada ${name} no contiene JSON valido.`);
    }
  };

  const list = (name) => {
    const value = required(name);
    try {
      const parsed = JSON.parse(value);
      if (Array.isArray(parsed) && parsed.length) {
        return parsed;
      }
    } catch (error) {
      // Tambien se admite una lista separada por comas.
    }
    const values = value.split(',').map((item) => item.trim()).filter(Boolean);
    if (!values.length) {
      throw new Error(`La propiedad privada ${name} esta vacia.`);
    }
    return values;
  };

  const github = (workflowProperty, defaultWorkflow) => ({
    owner: required('PUBLIC_RUNNER_OWNER'),
    repo: required('PUBLIC_RUNNER_REPO'),
    branch: optional('PUBLIC_RUNNER_BRANCH', 'main'),
    workflow: optional(workflowProperty, defaultWorkflow),
  });

  return { required, optional, json, optionalJson, list, github };
})();

function CONFIG_90_aplicarPropiedades(payload) {
  const allowed = new Set([
    'PUBLIC_RUNNER_OWNER',
    'PUBLIC_RUNNER_REPO',
    'PUBLIC_RUNNER_BRANCH',
    'FICHAS_GITHUB_WORKFLOW',
    'FICHAS_CALLBACK_TOKEN',
    'SEM_WORKSHEET_MAP_JSON',
    'SEM_CONTROL_LAYOUTS_JSON',
    'SEM_GLOBAL_SHEET',
    'CONTROL_SEM_SPREADSHEET_ID',
    'SEM_SALDO_RECIPIENTS',
    'SEM_END_DATE_RECIPIENTS',
    'SEM_LEGACY_BUTTON_HANDLERS_JSON',
  ]);
  const values = payload || {};
  Object.keys(values).forEach((name) => {
    if (!allowed.has(name) || !String(values[name] || '').trim()) {
      throw new Error(`Propiedad no permitida o vacia: ${name}`);
    }
  });
  PropertiesService.getScriptProperties().setProperties(values, false);
  Logger.log(
    `Configuracion privada actualizada: ${Object.keys(values).sort().join(', ')}`
  );
}

function CONFIG_91_auditarPropiedades() {
  [
    'GITHUB_TOKEN',
    'FICHAS_CALLBACK_TOKEN',
    'PUBLIC_RUNNER_OWNER',
    'PUBLIC_RUNNER_REPO',
    'SEM_WORKSHEET_MAP_JSON',
    'SEM_CONTROL_LAYOUTS_JSON',
    'SEM_GLOBAL_SHEET',
    'CONTROL_SEM_SPREADSHEET_ID',
    'SEM_SALDO_RECIPIENTS',
    'SEM_END_DATE_RECIPIENTS',
  ].forEach((name) => RuntimeConfig.required(name));
  fichasWorksheetMap_();
  RuntimeConfig.list('SEM_SALDO_RECIPIENTS');
  RuntimeConfig.list('SEM_END_DATE_RECIPIENTS');
  Logger.log('Configuracion privada de fichas/alertas correcta.');
}
