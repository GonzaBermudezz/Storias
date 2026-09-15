# Estado del proyecto — Portal Storias / Argo Media

Última actualización: 2026-09-15 (Gonza). Este documento es para que Matías retome el trabajo sin tener que preguntar por WhatsApp qué se hizo — se actualiza cada vez que se cierra un bloque. El contrato técnico completo (tablas, modelos Pydantic, firmas de función) sigue viviendo en `AGENTS.md`/`CLAUDE.md` en la raíz del repo — esto es el resumen humano de qué se hizo y qué falta.

## Dónde está el código

- Rama de trabajo: `codex/a2-engine-integration` (tiene adentro A1 + A2 + A3, ver abajo). Todavía no está mergeada a `main`.
- Si estás leyendo esto desde un fork (`github.com/GonzaBermudezz/Storias`), es porque Gonza todavía no tenía tu usuario como colaborador en `matiasavaca/Storias` al momento de hacer este trabajo — pediselo si no lo tenés, así se puede laburar directo sobre el repo original de acá en adelante.

## Qué se hizo, bloque por bloque

**A1 — Motor de contenido multi-cliente (listo, commit `12b117c`)**
Se sacó de `main.py` (el bot original de Cuan) todo lo que estaba hardcodeado para un solo cliente y pasó a `app/engine/`, que ahora recibe todo por parámetro (`ClientContentConfig`):
- `app/engine/schemas.py`, `exceptions.py`, `content.py`, `imaging.py`.
- `CLAUDE_MODEL = "claude-sonnet-5"` reemplaza el `"claude-sonnet-4-6"` original (no era un modelo válido).
- 17 tests verdes con los valores reales de CUAN como fixture, mockeando Claude/Cloudinary/Meta.
- El motor no importa nada de Supabase/Celery/Drive/FastAPI — es la regla dura del contrato.

**A2 — Cola de trabajo y scheduler (listo)**
Conecta el motor de A1 con el resto del sistema:
- Migración SQL: columnas nuevas en `clients` + tablas `client_images`, `employee_clients`, `prompt_history`.
- `app/services/drive.py`: integración con Google Drive (Shared Drives).
- `app/services/content_jobs.py`: generación semanal por cliente, aislando errores (un cliente con problemas no frena a los demás).
- `app/services/scheduler.py`: tareas de Celery — generación viernes 18:00, publicación diaria (configurable, default 09:00).
- 33 tests Python + 10 grupos de validación SQL (PGlite) en verde.
- **Pendiente**: la migración todavía no se aplicó a un Supabase real — no hay proyecto de Supabase creado todavía (es parte de la A0, ver abajo).

**A3 — No-repetición real de imágenes entre semanas**
*(Actualizar esta sección con el resultado una vez que termine de correr — quedó lanzado la noche del 15/09 en Codex, sobre esta misma rama)*
Debería resolver: selección de imágenes que prioriza las nunca usadas, después las usadas hace más de `NO_REPEAT_WEEKS` (3 por defecto), y si el pool se agota, recicla las más viejas en vez de frenar la generación (queda marcado como advertencia "pool_bajo" para un futuro dashboard, no como error). Reemplaza el `TODO(A3)` que había quedado en `content_jobs.py`.

## Qué NO está hecho todavía

- **A0 — Setup de infraestructura real**: no hay proyecto de Supabase creado, no hay Unidad Compartida de Google Drive con service account configurado. Todo lo de A1/A2/A3 está probado con mocks/tests, no corrió nunca contra servicios reales.
- **A4 — Portal de empleados**: conectar el mockup `opcion2-empleados.html` a datos reales — login de Google, `employee_clients` para que cada PM vea solo lo suyo, el campo de descripción de negocio + "enfoque de la semana", botón de "probar prompt", historial de cambios, edición/reorden de historias.
- **A5 — Piloto end-to-end**: 2-3 clientes reales de Felix corriendo un ciclo semanal completo.
- Fase B completa (conexión de Instagram self-serve con Facebook Login for Business — depende de que Felix haya iniciado el trámite de App Review de Meta —, alta masiva de clientes, dashboard de salud del sistema, rollout gradual).

## Decisiones ya tomadas (no reabrir sin avisar)

- Sin aprobación del cliente antes de publicar — se genera y publica solo.
- Los PMs de Felix manejan las cuentas de Instagram de sus clientes (no hay OAuth de cliente final en el MVP).
- Un solo Google Drive (Unidad Compartida) para toda Argo Media, una carpeta por cliente.
- `NO_REPEAT_WEEKS = 3` como default de no-repetición (fácil de cambiar, ver A3).

## Próximo paso sugerido para quien retome esto

Si sos Matías y estás leyendo esto: lo más útil ahora es (a) revisar el diff de esta rama contra `main`, (b) armar el proyecto de Supabase real y aplicar la migración de A2, y (c) si querés seguir con código, A4 (portal de empleados) es el bloque que más falta y el que más impacto visual tiene para mostrarle a Felix.
