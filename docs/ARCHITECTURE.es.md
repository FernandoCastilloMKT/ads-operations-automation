# Arquitectura De La Automatizacion

[English](ARCHITECTURE.md) | **Español**

## Alcance

La solucion sustituye consultas recurrentes de Supermetrics cuando se necesita
mas control que una extraccion tabular estandar. No pretende replicar todo
Supermetrics: implementa los flujos SEM concretos de consumo, rendimiento,
historico, saldos y comprobaciones operativas.

La version publica conserva las decisiones tecnicas y elimina toda la
configuracion de negocio.

## Capas

### 1. Fuentes

- Google Ads API para cuentas, campanas y costes.
- Microsoft Advertising API para campanas y costes adicionales.
- Google Calendar API para acciones SEM programadas.
- Google Sheets API para leer contratos de hoja y escribir resultados.

### 2. Dominio Python

Los scripts separan tres responsabilidades:

- `actualizar_consumos.py`: gasto diario y mensual por cuenta;
- `actualizar_fichas_sem.py`: tablas de rendimiento y bloques de saldo;
- `verificar_acciones_calendario_sem.py`: comprobaciones posteriores a una
  pausa o reactivacion.

La configuracion se carga desde JSON externo. El codigo recibe claves opacas y
no necesita conocer nombres reales.

### 3. Orquestacion

La arquitectura productiva usa dos mecanismos complementarios:

- GitHub Actions aporta un entorno reproducible que no depende de que un PC
  permanezca encendido.
- Google Apps Script aporta horarios, botones dentro de Sheets, avisos y
  bloqueo de solicitudes duplicadas.

Apps Script solicita una ejecucion con `workflow_dispatch`; GitHub prepara el
entorno y ejecuta el script Python correspondiente.

En este repositorio, los workflows operativos viven en `examples/workflows/`
con extension `.yml.example`. Son documentacion ejecutable, pero GitHub no los
reconoce como automatizaciones activas.

## Flujo De Consumos

1. Se carga la configuracion de MCC y hojas.
2. Se descubren cuentas finales, sin filtrar solo por `ENABLED`.
3. Se consulta gasto por cuenta y fecha.
4. Se agregan resultados de varias jerarquias cuando corresponde.
5. Se detectan dinamicamente encabezados, IDs y columnas de fecha.
6. Se actualizan el mes actual y el anterior.
7. Se escribe una matriz por bloque para reducir cuota y errores 429.
8. Al cambiar de mes o ano se crea la nueva hoja desde una plantilla.

La identidad estable es el ID de cuenta, no el nombre. Por eso repetir una
ejecucion actualiza filas existentes en vez de duplicarlas.

## Flujo De Fichas SEM

1. Se selecciona una ficha mediante una clave opaca.
2. Se consultan sus cuentas publicitarias en paralelo con limites controlados.
3. Se normalizan metricas de Google Ads y Microsoft Ads.
4. Se detecta el bloque vivo de la hoja por cabeceras y textos semanticos.
5. Se ajusta el numero de filas manteniendo formato y una fila de separacion.
6. Se escriben campanas y totales en bloque.
7. Se actualizan estado de cuenta, gasto real y periodo en curso.
8. En el cambio de periodo se archiva el bloque anterior siguiendo el orden
   cronologico.
9. Se mantiene la posicion de filas bajo control manual y se aplica formato
   monetario a costes agregados vivos e historicos.

Las renovaciones mensuales sin fecha fin avanzan normalmente el primer dia
del mes. Si se pierde esa ejecucion, una posterior recupera una fecha de un
mes anterior, respetando presupuestos manuales y sin adelantar el mes actual
antes del siguiente dia 1.

Las excepciones se expresan en configuracion. Esto evita que los nombres de
clientes se conviertan en ramas dispersas dentro del codigo.

## Fiabilidad

- Reintentos limitados ante errores transitorios de API.
- Escritura idempotente mediante IDs y rangos deterministas.
- Locks para impedir ejecuciones simultaneas.
- Actualizacion retrospectiva para absorber ajustes de facturacion.
- Logs detallados solo en infraestructura privada.
- Pruebas simuladas que no contactan con APIs.
- Acciones de terceros fijadas a hashes concretos.

## Modelo Publico

Este repositorio es source-visible y demostrativo:

- solo CI esta activo;
- no contiene GitHub Secrets;
- no esta vinculado con Apps Script productivo;
- usa configuraciones ficticias;
- cada publicacion se crea desde una lista cerrada de archivos;
- un auditor busca secretos, IDs, emails, rutas locales y valores privados
  antes de permitir la publicacion.

La infraestructura real, sus configuraciones y sus logs permanecen en un
repositorio privado independiente.
