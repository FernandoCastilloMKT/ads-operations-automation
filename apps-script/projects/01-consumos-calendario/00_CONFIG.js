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

  const github = (workflowProperty, defaultWorkflow) => ({
    owner: required('PUBLIC_RUNNER_OWNER'),
    repo: required('PUBLIC_RUNNER_REPO'),
    branch: optional('PUBLIC_RUNNER_BRANCH', 'main'),
    workflow: optional(workflowProperty, defaultWorkflow),
  });

  const json = (name) => {
    const value = required(name);
    try {
      return JSON.parse(value);
    } catch (error) {
      throw new Error(`La propiedad privada ${name} no contiene JSON valido.`);
    }
  };

  return { required, optional, github, json };
})();

function CONFIG_90_aplicarPropiedades(payload) {
  const allowed = new Set([
    'PUBLIC_RUNNER_OWNER',
    'PUBLIC_RUNNER_REPO',
    'PUBLIC_RUNNER_BRANCH',
    'CONSUMOS_GITHUB_WORKFLOW',
    'CONSUMOS_SPREADSHEET_CLIENT_MAP_JSON',
    'CONSUMOS_CALLBACK_TOKEN',
    'CALENDAR_GITHUB_WORKFLOW',
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
    'PUBLIC_RUNNER_OWNER',
    'PUBLIC_RUNNER_REPO',
    'CONSUMOS_SPREADSHEET_CLIENT_MAP_JSON',
    'CONSUMOS_CALLBACK_TOKEN',
  ].forEach((name) => RuntimeConfig.required(name));
  Logger.log('Configuracion privada de consumos/calendario correcta.');
}
