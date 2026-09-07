function doPost(event) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    return smmCallbackResponse_({ok: false, retryable: true, error: 'busy'});
  }
  try {
    const payload = smmCallbackPayload_(event);
    if (!smmCallbackSecretsEqual_(
      payload.token,
      RuntimeConfig.required('SMM_CALLBACK_TOKEN')
    )) {
      return smmCallbackResponse_({ok: false, error: 'unauthorized'});
    }
    const requestId = String(payload.request_id || '').trim();
    if (!requestId) {
      return smmCallbackResponse_({ok: false, error: 'missing_request_id'});
    }
    const conclusion = String(payload.conclusion || '').toLowerCase();
    if (!['success', 'failure', 'cancelled'].includes(conclusion)) {
      return smmCallbackResponse_({ok: false, error: 'invalid_conclusion'});
    }
    const properties = PropertiesService.getScriptProperties();
    const key = `${SMM_PENDING_PREFIX}${requestId}`;
    if (!properties.getProperty(key)) {
      return smmCallbackResponse_({ok: true, handled: false});
    }
    smmFinalizar_(properties, key, conclusion, String(payload.run_url || ''));
    return smmCallbackResponse_({ok: true, handled: true});
  } catch (error) {
    console.error(`Error procesando callback SMM: ${error.message}`);
    return smmCallbackResponse_({
      ok: false,
      retryable: true,
      error: 'internal_error',
    });
  } finally {
    lock.releaseLock();
  }
}

function smmCallbackPayload_(event) {
  try {
    return JSON.parse(event.postData.contents || '{}');
  } catch (error) {
    return {};
  }
}

function smmCallbackSecretsEqual_(received, expected) {
  const receivedDigest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    String(received || ''),
    Utilities.Charset.UTF_8
  );
  const expectedDigest = Utilities.computeDigest(
    Utilities.DigestAlgorithm.SHA_256,
    String(expected || ''),
    Utilities.Charset.UTF_8
  );
  let difference = receivedDigest.length ^ expectedDigest.length;
  for (let index = 0; index < receivedDigest.length; index += 1) {
    difference |= receivedDigest[index] ^ expectedDigest[index];
  }
  return difference === 0;
}

function smmCallbackResponse_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
