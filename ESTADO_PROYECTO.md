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

**A3 — No-repetición real de imágenes entre semanas (listo, verificado)**
- `seleccionar_imagenes(...)` prioriza: (1) imágenes nunca usadas, (2) imágenes fuera del cooldown, (3) si el pool está agotado, recicla las más antiguas (`last_used_at` más viejo primero) en vez de frenar la generación.
- `NO_REPEAT_WEEKS = 3` queda centralizado y configurable en un solo lugar.
- `generate_weekly()` ahora lee `client_images` antes de seleccionar (reemplaza el `TODO(A3)`) y persiste la advertencia `pool_bajo` cuando recicla, con marcador `TODO(B3)` para el futuro dashboard de salud.
- Confirmado: `app/engine/**`, migraciones y auth sin modificar.

**Suite completa verificada de punta a punta: `python -m pytest` en `C:\Storias` → 36/36 tests en verde** (17 de A1 + los de A2 + los de A3, corridos juntos en un estado limpio, con `pip install -r requirements.txt` recién hecho). Nota para quien corra esto de nuevo: usar `python -m pytest`, no `pytest` solo — con el comando `pytest` a secas da `ModuleNotFoundError: No module named 'app'` en este entorno.

## A0 — Infraestructura real (lista para desarrollo/piloto)

- Proyecto de Supabase real creado, schema + migración de A2 aplicados.
- Carpeta de Drive normal (no Unidad Compartida todavía — ver nota abajo) compartida con una service account, `GOOGLE_SERVICE_ACCOUNT_FILE` configurado, scope `drive.readonly`.
- Claude, Cloudinary y clave de encriptación cargados en `.env`.
- **Smoke test real de punta a punta corrido con éxito el 2026-09-15** (`scratchpad/smoke_real_a3.py`, no commiteado — script de un solo uso): bajó 4 imágenes reales de Drive, Claude generó 4 historias reales con el prompt de CUAN, se subieron 8 URLs reales a Cloudinary (4 crudas + 4 editadas), y quedó persistido en Supabase (`story_group` + 4 `stories` + 4 filas en `client_images`). Cero errores. **Confirma que A1+A2+A3 funcionan juntos contra servicios reales, no solo en tests mockeados.**
- Queda un cliente de prueba real en la tabla `clients` (`CUAN A3 Real Smoke...`, id `628e4d79-f55c-4f1b-9210-f28f77777b2b`) — decidir si se borra o se deja de referencia antes de sumar clientes reales de Felix.
- **Nota**: por ahora la carpeta de Drive es una carpeta común (no Unidad Compartida), porque el plan de Google Workspace de Argo Media todavía no está confirmado — ver sección de decisiones abajo. Migrar a Unidad Compartida antes de sumar clientes reales de producción.

## Qué NO está hecho todavía
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