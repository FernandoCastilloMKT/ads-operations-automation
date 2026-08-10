function doPost(event) {
  const lock = LockService.getScriptLock();
  if (!lock.tryLock(5000)) {
    return consumosCallbackResponse_({
      ok: false,
      retryable: true,
      error: 'busy',
    });
  }

  try {
    const payload = consumosCallbackPayload_(event);
    if (
      !consumosCallbackSecretsEqual_(
        payload.token,
        RuntimeConfig.required('CONSUMOS_CALLBACK_TOKEN')
      )
    ) {
      return consumosCallbackResponse_({ ok: false, error: 'unauthorized' });
    }

    const requestId = String(payload.request_id || '').trim();
    if (!requestId) {
      return consumosCallbackResponse_({
        ok: false,
        error: 'missing_request_id',
      });
    }

    const conclusion = String(payload.conclusion || '').toLowerCase();
    if (!['success', 'failure', 'cancelled'].includes(conclusion)) {
      return consumosCallbackResponse_({
        ok: false,
        error: 'invalid_conclusion',
      });
    }

    const properties = PropertiesService.getScriptProperties();
    const pendingKey = `${CONSUMOS_PENDING_PROPERTY_PREFIX}${requestId}`;
    const pendingRaw = properties.getProperty(pendingKey);
    if (!pendingRaw) {
      return consumosCallbackResponse_({
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
      return consumosCallbackResponse_({
        ok: false,
        error: 'invalid_pending_state',
      });
    }

    consumosFinalizarPendiente_(properties, pendingKey, pending, {
      conclusion: conclusion,
      runUrl: String(payload.run_url || ''),
      finishedAt: payload.finished_at || new Date(),
    });

    const remaining = properties
      .getKeys()
      .some((key) => key.startsWith(CONSUMOS_PENDING_PROPERTY_PREFIX));
    if (!remaining) {
      consumosEliminarActivadoresFinalizacion_();
    }

    Logger.log(`Callback GitHub consumos: ${requestId} = ${conclusion}.`);
    return consumosCallbackResponse_({ ok: true, handled: true });
  } catch (error) {
    console.error(`Error procesando callback de consumos: ${error.message}`);
    return consumosCallbackResponse_({
      ok: false,
      retryable: true,
      error: 'internal_error',
    });
  } finally {
    lock.releaseLock();
  }
}

function consumosCallbackPayload_(event) {
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

function consumosCallbackSecretsEqual_(received, expected) {
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

function consumosCallbackResponse_(payload) {
  return ContentService.createTextOutput(JSON.stringify(payload))
    .setMimeType(ContentService.MimeType.JSON);
}
