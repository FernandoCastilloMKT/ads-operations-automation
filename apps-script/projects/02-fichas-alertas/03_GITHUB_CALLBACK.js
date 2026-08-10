function doPost(event) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(SEM_DISPATCH_LOCK_WAIT_MS)) {
    return fichasCallbackResponse_({
      ok: false,
      retryable: true,
      error: 'busy',
    });
  }

  try {
    const payload = fichasCallbackPayload_(event);
    if (
      !fichasCallbackSecretsEqual_(
        payload.token,
        RuntimeConfig.required('FICHAS_CALLBACK_TOKEN')
      )
    ) {
      return fichasCallbackResponse_({ ok: false, error: 'unauthorized' });
    }

    const requestId = String(payload.request_id || '').trim();
    if (!requestId) {
      return fichasCallbackResponse_({ ok: false, error: 'missing_request_id' });
    }

    const conclusion = String(payload.conclusion || '').toLowerCase();
    if (!['success', 'failure', 'cancelled'].includes(conclusion)) {
      return fichasCallbackResponse_({ ok: false, error: 'invalid_conclusion' });
    }

    const properties = PropertiesService.getScriptProperties();
    const pendingKey = `${SEM_PENDING_PROPERTY_PREFIX}${requestId}`;
    const pendingRaw = properties.getProperty(pendingKey);
    if (!pendingRaw) {
      return fichasCallbackResponse_({
        ok: true,
        handled: false,
        reason: 'not_pending',
      });
    }

    let pending;
    try {
      pending = JSON.parse(pendingRaw);
    } catch (error) {
      properties.deleteProperty(pendingKey);
      return fichasCallbackResponse_({
        ok: false,
        error: 'invalid_pending_state',
      });
    }

    fichasFinalizarPendiente_(properties, pendingKey, pending, {
      conclusion: conclusion,
      runUrl: String(payload.run_url || ''),
      finishedAt: payload.finished_at || new Date(),
    });

    const remaining = properties
      .getKeys()
      .some((key) => key.startsWith(SEM_PENDING_PROPERTY_PREFIX));
    if (!remaining) {
      fichasEliminarActivadoresFinalizacion_();
    }

    Logger.log(`Callback GitHub procesado: ${requestId} = ${conclusion}.`);
    return fichasCallbackResponse_({ ok: true, handled: true });
  } catch (error) {
    console.error(`Error procesando callback GitHub: ${error.message}`);
    return fichasCallbackResponse_({
      ok: false,
      retryable: true,
      error: 'internal_error',
    });
  } finally {
    lock.releaseLock();
  }
}

function fichasCallbackPayload_(event) {
  const body = event && event.postData && event.postData.contents;
  if (!body) {
    return {};
  }
  try {
    return JSON.parse(body);
  } catch (error) {
    return {};
  }
}

function fichasCallbackSecretsEqual_(received, expected) {
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

function fichasCallbackResponse_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
