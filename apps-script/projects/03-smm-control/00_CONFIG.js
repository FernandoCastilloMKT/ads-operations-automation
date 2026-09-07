const RuntimeConfig = Object.freeze({
  required(name) {
    const value = PropertiesService.getScriptProperties().getProperty(name);
    if (!value || !String(value).trim()) {
      throw new Error(`Falta la propiedad privada ${name}.`);
    }
    return String(value).trim();
  },
  github(workflowProperty, fallback) {
    return {
      owner: this.required('PUBLIC_RUNNER_OWNER'),
      repo: this.required('PUBLIC_RUNNER_REPO'),
      branch: this.required('PUBLIC_RUNNER_BRANCH'),
      workflow: PropertiesService.getScriptProperties().getProperty(
        workflowProperty
      ) || fallback,
    };
  },
});

function CONFIG_90_aplicarPropiedades(values) {
  if (!values || typeof values !== 'object' || Array.isArray(values)) {
    throw new Error('La configuracion debe ser un objeto.');
  }
  const allowed = new Set([
    'PUBLIC_RUNNER_OWNER',
    'PUBLIC_RUNNER_REPO',
    'PUBLIC_RUNNER_BRANCH',
    'SMM_GITHUB_WORKFLOW',
    'SMM_SPREADSHEET_ID',
    'SMM_CALLBACK_TOKEN',
    'GITHUB_TOKEN',
  ]);
  const properties = {};
  Object.keys(values).forEach((key) => {
    if (!allowed.has(key)) throw new Error(`Propiedad no permitida: ${key}`);
    properties[key] = String(values[key]);
  });
  PropertiesService.getScriptProperties().setProperties(properties, false);
  return 'Propiedades SMM aplicadas.';
}
