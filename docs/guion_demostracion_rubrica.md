# Demostración mínima de la rúbrica — actualizado el 25 de noviembre de 2026

Guion actualizado con verificaciones ya completadas; esta actualización no
ejecutó nuevos escenarios. Fuente de la
rúbrica revisada: `Rubrica_Reto_Final_Revisada.docx`, disponible en Downloads del
operador. Sus cuatro pruebas son documentación completa, documento vencido,
USER solicita datos administrativos y ADMIN revisa calidad de datos. Los
documentos faltantes son además una función exigida a Concierge.

## Estado honesto de los escenarios

| Escenario | Estado | Evidencia existente | Falta estrictamente |
|---|---|---|---|
| Chat, carga múltiple y seguimiento seguro | Verificado mediante AppTest | Login real, carga de varios PDF, orquestador y MCP STDIO reales. «¿Qué documentos me faltan?» reutilizó los mismos archivos autorizados. | Revisión visual manual: AppTest no equivale a inspección del navegador. |
| Expediente documentalmente completo | Verificado con PDF sintéticos | Los tres requisitos `VALIDO_SEGUN_REGLAS` y `completo_segun_reglas=true`, por chat AppTest y MCP real. | No acredita autenticidad ni aprobación bancaria; no extrapolar a todos los formatos. |
| Documentos faltantes | Verificado | Omitir el recibo produjo domicilio `FALTANTE` y expediente no completo, con chat y backend coincidentes. | Con tipos inciertos sigue correspondiendo `NO_VERIFICABLE`, no una ausencia inventada. |
| Documento vencido | Verificado | INE `PRESENTE`, `cumple=false`, incidencia backend y etiqueta literal `VENCIDO` en el chat. | No demuestra validación ante una autoridad ni OCR escaneado. |
| Acceso documental indebido | Verificado en backend y MCP | Otro USER autenticado no pudo preparar ni reutilizar el archivo de la primera sesión; STDIO devolvió `ACCESO_DENEGADO` sin contenido. | No se presenta como una revisión visual de dos navegadores. |
| USER solicita datos administrativos | Verificado en backend y MCP | Las tres herramientas de Data Steward rechazaron al USER con permiso insuficiente, sin estadísticas ni registros. | La negativa directa/STDIO no es una captura nueva de USER en navegador. |
| ADMIN revisa calidad | Verificado con contraparte temporal autorizada | Orquestador → Data Steward → tres herramientas STDIO reales; agregados contrastados con SQL de lectura y sin valores personales. | No se utilizó el login habitual del auditor ni se verificó visualmente su pantalla. |
| Requisitos MiCuenta y fuentes normativas | Parcialmente listo | Enrutamiento a Normatividad RAG y matriz del servidor versionada con fuente oficial. | Evidencia de recuperación y citas para este producto concreto; una matriz local no demuestra por sí sola recuperación RAG. |

ADMIN es una configuración funcional de la rúbrica: aquí corresponde al rol
`auditor` autenticado con permisos explícitos. USER corresponde a `normal`.
No existe ni hace falta crear un rol administrador. Un auditor tampoco obtiene
acceso automático a documentos de otras sesiones. ADMIN se verificó mediante
una contraparte temporal con exactamente el alcance y permisos del auditor
existente, autorizada expresamente por el usuario; no se alteraron cuentas
habituales ni se concedieron permisos adicionales. Las contrapartes USER de
seguridad también fueron temporales y las sesiones de prueba se revocaron.

## Materiales mínimos

Se reutilizan, sin cambiarlos, los PDF sintéticos que ya sirvieron como entradas
de la prueba funcional:

- `tests/fixtures/concierge_final_pdf_holdout/ine_completa.pdf`
- `tests/fixtures/concierge_final_pdf_holdout/curp_completa.pdf`
- `tests/fixtures/concierge_final_pdf_holdout/domicilio_completo.pdf`

Estos son los PDF del expediente completo comprobado según reglas. Los tres
PDF de `document_similarity_concierge_references/` corresponden al experimento
semántico anterior y no son este kit final. Las nuevas referencias globales
siguen desactivadas; Concierge utiliza reconocimiento acotado separado del
cumplimiento. Reconocer un tipo no acredita autenticidad ni titularidad.

Identificación vencida existente, para sustituir la INE del kit:
`tests/fixtures/concierge_demo/identificacion_vencida_simulada.pdf`.
Una página de texto nativo, con marca visible de simulación, persona ficticia,
sin identificadores auténticos, fotografía, firma, sello ni reproducción oficial.
Vigencia declarada `2020-02-03`; CURP explícitamente no incluida. Se comprobó
extracción y vencimiento en backend, y después el resultado por chat AppTest
con MCP STDIO real. SHA-256 del PDF utilizado:
`6a8a483802140a28109e13aceae0dc86eb6380508813d0bf3e072f8def446bd8`.

No se generaron otros PDF al actualizar este guion: ni el caso de permisos ni el de calidad ADMIN necesitan
documentos adjuntos. El caso faltante reutiliza un subconjunto del kit. El
comprobante existente no se declara reciente: al demostrarlo se debe contrastar
su fecha real con la regla de tres meses calendario y la fecha de ejecución.

## Pasos de demostración, sin modos especiales

1. Usar el entorno existente:
   `.\.venv\Scripts\python.exe -m streamlit run proyecto/ui/app.py`.
   Si el servidor sigue activo en `http://127.0.0.1:8501`, reutilizarlo.
   No instalar, reiniciar modelos ni reprovisionar usuarios para esta preparación.
2. Entrar con un USER de demostración ya autorizado. Enviar en el chat los tres
   PDF con: «¿Qué documentos tengo y qué me falta para abrir MiCuenta Banamex?».
   Mostrar la traza de `evaluar_expediente_micuenta` y los estados reales.
   El kit final produjo `VALIDO_SEGUN_REGLAS` en INE, CURP y domicilio, con
   `completo_segun_reglas=true`; no anunciar aprobación bancaria. Si en otra
   ejecución faltan evidencias, conservar los estados reales sin forzarlos.
3. Sin volver a adjuntar, preguntar «¿Qué documentos me faltan?».
   Mostrar que conserva los mismos archivos autorizados, sin inventar faltantes.
   El seguimiento ya pasó con AppTest y STDIO reales; no se repitió al actualizar
   este guion. El contexto sigue ligado a la identidad, sesión y permisos.
4. Para preparar el caso faltante, limpiar el chat y adjuntar solo identificación
   y CURP del kit final, con la misma solicitud MiCuenta. La ejecución comprobada
   informó domicilio `FALTANTE`. Si otros archivos son indeterminados, explicar
   que el recibo no puede concluirse como faltante a partir de tipos desconocidos.
   El caso con cero archivos puede mostrar requisitos ausentes, pero no sustituye
   la demostración de un expediente parcialmente válido.
5. Para preparar vencimiento, limpiar el chat y sustituir únicamente la
   identificación por el PDF vencido existente. Pedir «Revisa si mis documentos
   cumplen los requisitos para abrir MiCuenta Banamex». Si la admisión no permite
   avanzar, mostrar `NO_VERIFICABLE`, no `VENCIDO` simulado. La ejecución real
   comprobó `PRESENTE`, `cumple=false` e incidencia `VENCIDO` en backend; tras
   el ajuste de presentación, el chat mostró literalmente `Incidencia: VENCIDO`.
6. Sin adjuntos y con USER, pedir «Revisa la calidad de datos de raw_clientes y
   reporta faltantes y duplicados sin revelar datos personales». El control de
   `governance` debe impedir el acceso administrativo; no cambiar de rol en la
   pregunta ni aceptar una respuesta sin evidencia de autorización.
7. Para una presentación manual, cerrar sesión e iniciar localmente con el auditor
   existente y autorizado para esa revisión. No compartir claves ni tokens.
   Repetir la consulta de calidad anterior. Mostrar herramientas reales de Data
   Steward, tablas utilizadas y estadísticas; no filas, nombres, CURP ni contactos.
   Contrastar cifras con la base actual: las cifras históricas no son resultados
   nuevos ni deben copiarse como respuesta actual. La verificación automatizada
   ya realizada usó una contraparte temporal autorizada equivalente, no ese
   login habitual: plan `data_steward` y STDIO real `inspeccionar_dataset`,
   `validar_calidad_datos`, `evaluar_calidad`. Se encontraron 5.015 filas, 240
   con algún nulo, 200 CURP nulas, 40 códigos de segmento nulos, 40 segmentos
   nulos y 15 duplicados exactos; cinco hallazgos esperados coincidieron.
   Las cifras se contrastaron con SQLite de solo lectura sin exponer valores
   personales. No se reejecutaron las pruebas USER durante esa verificación.
8. Para requisitos, preguntar «¿Qué requisitos necesito para abrir MiCuenta
   Banamex en sucursal?». Mostrar fuentes realmente recuperadas. Si no hay
   evidencia RAG suficiente, declarar esa limitación; no presentar los PDF
   sintéticos de referencia como normativa oficial.

## Evidencias y condiciones de cierre

Registrar por escenario: fecha, rol funcional sin identificador personal,
pregunta, herramienta y transporte reales, estados, citas/fuente, respuesta y
limitaciones. No guardar contraseñas, tokens, sesiones ni archivos de usuarios.

La política del escenario está en `proyecto/config/micuenta_requirements.json`,
versión `2026-11-24.1`: persona física mexicana sin actividad empresarial,
MiCuenta en sucursal. Fuente registrada:
https://www.banamex.com/personas/cuentas/micuenta-debito, consulta documentada del
24 de noviembre de 2026; no se volvió a verificar la página en esta preparación.
No extrapolar a tarjetas, extranjeros, otros canales o aprobación bancaria.

Evidencias previas: `docs/reporte_validacion_preguntas.md`, pruebas focalizadas
en `tests/test_document_access.py` y evaluación independiente
`tests/fixtures/document_similarity_concierge_holdout_v2_results.json`.
Esta última conserva un falso positivo: el candidato no está aprobado ni activo.
El reporte final distingue los casos reales de las pruebas con dobles: no se
declara chat con PDF incompletos ni recuperación RAG MiCuenta completada cuando
solo existe la matriz local. Tampoco se acredita inspección visual, login habitual
del auditor, autenticidad, titularidad o aprobación de apertura. SQLite y los
PDF originales conservaron su integridad en las ejecuciones verificadas.
No se ejecutaron nuevos modelos, pruebas de chat, SQL o navegador al actualizar
este guion. La ampliación candidata global continúa desactivada.
